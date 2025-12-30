"""GitHub API Client - Thin wrapper around GitHub REST API."""

import os
import aiohttp
from typing import Any, Dict, List, Optional


class GitHubClient:
    """Async GitHub API client."""
    
    BASE_URL = "https://api.github.com"
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "graph-mcp-server"
        }
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make API request."""
        url = f"{self.BASE_URL}{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=self.headers, **kwargs) as resp:
                if resp.status == 404:
                    return {"error": "Not found", "status": 404}
                elif resp.status == 401:
                    return {"error": "Unauthorized - check GITHUB_TOKEN", "status": 401}
                elif resp.status == 403:
                    return {"error": "Forbidden - rate limit or permissions", "status": 403}
                elif resp.status >= 400:
                    text = await resp.text()
                    return {"error": text, "status": resp.status}
                
                # Handle diff response (plain text)
                if "diff" in kwargs.get("headers", {}).get("Accept", ""):
                    return {"diff": await resp.text()}
                
                return await resp.json()
    
    async def get_file_contents(self, owner: str, repo: str, path: str, 
                                 branch: Optional[str] = None) -> Dict[str, Any]:
        """
        GET /repos/{owner}/{repo}/contents/{path}
        Returns file contents (base64 encoded for files, list for directories).
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{path}"
        params = {}
        if branch:
            params["ref"] = branch
        
        result = await self._request("GET", endpoint, params=params)
        
        if "error" in result:
            return result
        
        # Decode base64 content if it's a file
        if isinstance(result, dict) and result.get("type") == "file":
            import base64
            try:
                content = base64.b64decode(result.get("content", "")).decode("utf-8")
                return {
                    "path": result.get("path"),
                    "name": result.get("name"),
                    "sha": result.get("sha"),
                    "size": result.get("size"),
                    "content": content,
                    "encoding": "utf-8"
                }
            except Exception:
                return result
        
        # Directory listing
        if isinstance(result, list):
            return {
                "type": "directory",
                "path": path,
                "contents": [
                    {"name": f.get("name"), "type": f.get("type"), "path": f.get("path")}
                    for f in result
                ]
            }
        
        return result
    
    async def search_repositories(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """
        GET /search/repositories
        Search GitHub repositories.
        """
        endpoint = "/search/repositories"
        params = {"q": query, "per_page": min(limit, 100)}
        
        result = await self._request("GET", endpoint, params=params)
        
        if "error" in result:
            return result
        
        return {
            "total_count": result.get("total_count", 0),
            "repositories": [
                {
                    "full_name": r.get("full_name"),
                    "description": r.get("description"),
                    "url": r.get("html_url"),
                    "stars": r.get("stargazers_count"),
                    "language": r.get("language"),
                    "updated_at": r.get("updated_at")
                }
                for r in result.get("items", [])[:limit]
            ]
        }
    
    async def create_pull_request(self, owner: str, repo: str, title: str,
                                   body: str, head: str, base: str) -> Dict[str, Any]:
        """
        POST /repos/{owner}/{repo}/pulls
        Create a pull request.
        """
        endpoint = f"/repos/{owner}/{repo}/pulls"
        data = {
            "title": title,
            "body": body,
            "head": head,
            "base": base
        }
        
        result = await self._request("POST", endpoint, json=data)
        
        if "error" in result:
            return result
        
        return {
            "number": result.get("number"),
            "url": result.get("html_url"),
            "state": result.get("state"),
            "title": result.get("title"),
            "created_at": result.get("created_at")
        }
    
    async def list_commits(self, owner: str, repo: str, 
                           path: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
        """
        GET /repos/{owner}/{repo}/commits
        List commits, optionally filtered by path.
        """
        endpoint = f"/repos/{owner}/{repo}/commits"
        params = {"per_page": min(limit, 100)}
        if path:
            params["path"] = path
        
        result = await self._request("GET", endpoint, params=params)
        
        if "error" in result:
            return result
        
        if not isinstance(result, list):
            return {"error": "Unexpected response format"}
        
        return {
            "commits": [
                {
                    "sha": c.get("sha", "")[:7],
                    "message": c.get("commit", {}).get("message", "").split("\n")[0],
                    "author": c.get("commit", {}).get("author", {}).get("name"),
                    "date": c.get("commit", {}).get("author", {}).get("date"),
                    "url": c.get("html_url")
                }
                for c in result[:limit]
            ]
        }
    
    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """
        GET /repos/{owner}/{repo}/pulls/{pull_number}
        Get pull request details.
        """
        endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        result = await self._request("GET", endpoint)
        
        if "error" in result:
            return result
        
        # Also get files changed
        files_endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        files_result = await self._request("GET", files_endpoint)
        
        changed_files = []
        if isinstance(files_result, list):
            changed_files = [f.get("filename") for f in files_result]
        
        return {
            "number": result.get("number"),
            "title": result.get("title"),
            "state": result.get("state"),
            "body": result.get("body"),
            "user": result.get("user", {}).get("login"),
            "url": result.get("html_url"),
            "head": result.get("head", {}).get("ref"),
            "base": result.get("base", {}).get("ref"),
            "created_at": result.get("created_at"),
            "updated_at": result.get("updated_at"),
            "mergeable": result.get("mergeable"),
            "additions": result.get("additions"),
            "deletions": result.get("deletions"),
            "changed_files": changed_files
        }
    
    async def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """
        GET /repos/{owner}/{repo}/pulls/{pull_number}
        Get pull request diff (Accept: application/vnd.github.diff).
        """
        endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        
        # Override Accept header for diff format
        headers = {**self.headers, "Accept": "application/vnd.github.diff"}
        
        url = f"{self.BASE_URL}{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status >= 400:
                    return {"error": f"Failed to get diff: {resp.status}"}
                
                diff_text = await resp.text()
                return {"diff": diff_text, "pr_number": pr_number}
    
    async def get_repo_contents(self, owner: str, repo: str, path: str = "") -> Dict[str, Any]:
        """Get repository contents at path."""
        return await self.get_file_contents(owner, repo, path)
    
    async def list_branches(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        GET /repos/{owner}/{repo}/branches
        List repository branches.
        """
        endpoint = f"/repos/{owner}/{repo}/branches"
        result = await self._request("GET", endpoint)
        
        if "error" in result:
            return result
        
        if not isinstance(result, list):
            return {"error": "Unexpected response format"}
        
        return {
            "branches": [
                {"name": b.get("name"), "protected": b.get("protected")}
                for b in result
            ]
        }
