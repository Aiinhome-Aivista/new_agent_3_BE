import requests
import base64
import logging

logger = logging.getLogger(__name__)

class JiraService:
    @staticmethod
    def _get_headers(email, api_token):
        auth_str = f"{email}:{api_token}"
        b64_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        return {
            "Authorization": f"Basic {b64_auth}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    @staticmethod
    def _clean_url(domain_url):
        url = (domain_url or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"
        return url.rstrip('/')

    @classmethod
    def test_connection(cls, domain_url, email, api_token):
        base_url = cls._clean_url(domain_url)
        endpoint = f"{base_url}/rest/api/3/myself"
        headers = cls._get_headers(email, api_token)

        try:
            res = requests.get(endpoint, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                return {
                    "success": True,
                    "message": "Successfully connected to Jira!",
                    "user": {
                        "displayName": data.get("displayName"),
                        "emailAddress": data.get("emailAddress"),
                        "accountId": data.get("accountId"),
                        "avatarUrl": data.get("avatarUrls", {}).get("48x48")
                    }
                }
            else:
                return {
                    "success": False,
                    "message": f"Failed to authenticate with Jira (HTTP {res.status_code}): {res.text}"
                }
        except Exception as e:
            logger.error(f"Error testing Jira connection: {str(e)}")
            return {"success": False, "message": f"Connection error: {str(e)}"}

    @classmethod
    def get_projects(cls, domain_url, email, api_token):
        base_url = cls._clean_url(domain_url)
        endpoint = f"{base_url}/rest/api/3/project"
        headers = cls._get_headers(email, api_token)

        try:
            res = requests.get(endpoint, headers=headers, timeout=10)
            if res.status_code == 200:
                projects_raw = res.json()
                projects = [
                    {
                        "id": p.get("id"),
                        "key": p.get("key"),
                        "name": p.get("name"),
                        "projectTypeKey": p.get("projectTypeKey"),
                        "avatarUrl": p.get("avatarUrls", {}).get("48x48")
                    }
                    for p in projects_raw
                ]
                return {"success": True, "projects": projects}
            else:
                return {"success": False, "message": f"Failed to fetch projects (HTTP {res.status_code})"}
        except Exception as e:
            logger.error(f"Error fetching Jira projects: {str(e)}")
            return {"success": False, "message": str(e)}

    @classmethod
    def fetch_issues(cls, domain_url, email, api_token, project_key=None, jql=None, max_results=100):
        base_url = cls._clean_url(domain_url)
        headers = cls._get_headers(email, api_token)

        if not jql:
            if project_key:
                jql = f'project = "{project_key}" ORDER BY created DESC'
            else:
                jql = 'ORDER BY created DESC'

        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["summary", "status", "assignee", "priority", "issuetype", "description", "created", "duedate", "parent"]
        }

        # Try new Jira Cloud API endpoints for search (POST /rest/api/3/search/jql, POST /rest/api/3/search, GET /rest/api/3/search/jql)
        res = None
        endpoints_to_try = [
            ("POST", f"{base_url}/rest/api/3/search/jql", {"json": payload}),
            ("POST", f"{base_url}/rest/api/3/search", {"json": payload}),
            ("GET", f"{base_url}/rest/api/3/search/jql", {"params": {"jql": jql, "maxResults": max_results, "fields": "*all"}}),
            ("GET", f"{base_url}/rest/api/3/search", {"params": {"jql": jql, "maxResults": max_results, "fields": "*all"}})
        ]

        for method, url, kw in endpoints_to_try:
            try:
                if method == "POST":
                    res = requests.post(url, headers=headers, timeout=15, **kw)
                else:
                    res = requests.get(url, headers=headers, timeout=15, **kw)
                
                if res.status_code == 200:
                    break
            except Exception as req_err:
                logger.warning(f"Failed Jira endpoint {url}: {req_err}")

        try:
            if res and res.status_code == 200:
                data = res.json()
                raw_issues = data.get("issues", [])
                
                # First pass: map issue key -> summary & issueType for quick resolution
                issue_map = {}
                for issue in raw_issues:
                    ikey = issue.get("key")
                    if ikey:
                        f = issue.get("fields", {})
                        issue_map[ikey] = {
                            "id": issue.get("id"),
                            "key": ikey,
                            "summary": f.get("summary", ""),
                            "issueType": f.get("issuetype", {}).get("name", "Task") if f.get("issuetype") else "Task"
                        }

                issues = []
                parents_dict = {}
                missing_parent_keys = set()

                for issue in raw_issues:
                    fields = issue.get("fields", {})
                    
                    # Extract text description if Atlassian document format (ADF)
                    desc = ""
                    desc_field = fields.get("description")
                    if isinstance(desc_field, dict):
                        try:
                            paragraphs = []
                            for block in desc_field.get("content", []):
                                for content_item in block.get("content", []):
                                    if content_item.get("type") == "text":
                                        paragraphs.append(content_item.get("text", ""))
                            desc = " ".join(paragraphs)
                        except Exception:
                            desc = str(desc_field)
                    elif isinstance(desc_field, str):
                        desc = desc_field

                    # Extract parent issue details if available
                    parent_info = None
                    parent_field = fields.get("parent")
                    if isinstance(parent_field, dict):
                        parent_key = parent_field.get("key")
                        parent_summary = (
                            parent_field.get("fields", {}).get("summary") or
                            issue_map.get(parent_key, {}).get("summary") or
                            ""
                        )
                        if not parent_summary and parent_key:
                            missing_parent_keys.add(parent_key)
                            parent_summary = f"Parent {parent_key}"

                        parent_info = {
                            "id": parent_field.get("id"),
                            "key": parent_key,
                            "summary": parent_summary
                        }

                        if parent_key and parent_key not in parents_dict:
                            parents_dict[parent_key] = {
                                "id": parent_field.get("id"),
                                "key": parent_key,
                                "summary": parent_summary,
                                "issueType": "Parent"
                            }

                    # Check if issue itself is an Epic or Parent type
                    issue_type = fields.get("issuetype", {}).get("name", "Task") if fields.get("issuetype") else "Task"
                    if issue_type.lower() in ["epic", "parent", "initiative", "feature"]:
                        pkey = issue.get("key")
                        if pkey and pkey not in parents_dict:
                            parents_dict[pkey] = {
                                "id": issue.get("id"),
                                "key": pkey,
                                "summary": fields.get("summary", ""),
                                "issueType": issue_type
                            }

                    issues.append({
                        "id": issue.get("id"),
                        "key": issue.get("key"),
                        "summary": fields.get("summary", ""),
                        "description": desc,
                        "status": fields.get("status", {}).get("name", "Unknown") if fields.get("status") else "Unknown",
                        "statusCategory": fields.get("status", {}).get("statusCategory", {}).get("name") if fields.get("status") else "",
                        "assignee": fields.get("assignee", {}).get("displayName", "Unassigned") if fields.get("assignee") else "Unassigned",
                        "assigneeEmail": fields.get("assignee", {}).get("emailAddress", "") if fields.get("assignee") else "",
                        "priority": fields.get("priority", {}).get("name", "Medium") if fields.get("priority") else "Medium",
                        "issueType": issue_type,
                        "created": fields.get("created"),
                        "dueDate": fields.get("duedate"),
                        "parent": parent_info
                    })

                # Resolve any missing parent summaries directly via API if needed
                for pkey in missing_parent_keys:
                    try:
                        p_res = requests.get(f"{base_url}/rest/api/3/issue/{pkey}", headers=headers, timeout=5)
                        if p_res.status_code == 200:
                            p_data = p_res.json()
                            p_sum = p_data.get("fields", {}).get("summary", "")
                            if p_sum:
                                if pkey in parents_dict:
                                    parents_dict[pkey]["summary"] = p_sum
                                for iss in issues:
                                    if iss.get("parent") and iss["parent"]["key"] == pkey:
                                        iss["parent"]["summary"] = p_sum
                    except Exception as p_err:
                        logger.warning(f"Could not fetch summary for parent {pkey}: {p_err}")

                parents_list = list(parents_dict.values())

                return {
                    "success": True,
                    "issues": issues,
                    "parents": parents_list,
                    "total": data.get("total", len(issues))
                }
            else:
                err_msg = res.text if res else "No response from Jira server"
                status_code = res.status_code if res else 500
                return {"success": False, "message": f"Failed to fetch issues (HTTP {status_code}): {err_msg}"}
        except Exception as e:
            logger.error(f"Error fetching Jira issues: {str(e)}")
            return {"success": False, "message": str(e)}
