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
    def fetch_issues(cls, domain_url, email, api_token, project_key=None, jql=None, max_results=50):
        base_url = cls._clean_url(domain_url)
        endpoint = f"{base_url}/rest/api/3/search"
        headers = cls._get_headers(email, api_token)

        if not jql:
            if project_key:
                jql = f'project = "{project_key}" ORDER BY created DESC'
            else:
                jql = 'ORDER BY created DESC'

        params = {
            "jql": jql,
            "maxResults": max_results,
            "fields": "summary,status,assignee,priority,issuetype,description,created,duedate"
        }

        try:
            res = requests.get(endpoint, headers=headers, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                raw_issues = data.get("issues", [])
                issues = []
                for issue in raw_issues:
                    fields = issue.get("fields", {})
                    
                    # Extract text description if Atlassian document format (ADF)
                    desc = ""
                    desc_field = fields.get("description")
                    if isinstance(desc_field, dict):
                        # Extract simple paragraph text from ADF
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
                        "issueType": fields.get("issuetype", {}).get("name", "Task") if fields.get("issuetype") else "Task",
                        "created": fields.get("created"),
                        "dueDate": fields.get("duedate")
                    })
                return {"success": True, "issues": issues, "total": data.get("total", len(issues))}
            else:
                return {"success": False, "message": f"Failed to fetch issues (HTTP {res.status_code}): {res.text}"}
        except Exception as e:
            logger.error(f"Error fetching Jira issues: {str(e)}")
            return {"success": False, "message": str(e)}
