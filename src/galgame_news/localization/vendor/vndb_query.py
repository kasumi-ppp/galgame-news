#!/usr/bin/env python3
"""
VNDB API Query Tool - Python Version
Lightweight VNDB (Visual Novel Database) API v2 (Kana) query script
Replacement for curl commands in sandbox environments

Usage:
    python vndb_query.py <command> [arguments]

Quick Commands:
    python vndb_query.py character <keyword>              # Search characters
    python vndb_query.py vn <keyword>                     # Search visual novels
    python vndb_query.py vn_id <ID>                       # Get VN by ID
    python vndb_query.py latest [count]                   # Latest releases
    python vndb_query.py stats                            # Database statistics
    python vndb_query.py user <username>                  # Query user

Advanced Usage:
    python vndb_query.py query <endpoint> <filters> <fields> [sort] [results]

Examples:
    python vndb_query.py character "美雪"
    python vndb_query.py vn "Steins;Gate"
    python vndb_query.py vn_id "v17"
    python vndb_query.py latest 5
    python vndb_query.py stats
    python vndb_query.py user "yorhel"
"""

import sys
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from urllib.parse import urlencode


# API Configuration
API_BASE_URL = "https://api.vndb.org/kana"
DEFAULT_TIMEOUT = 30  # seconds


# Color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color


def error(message: str) -> None:
    """Print error message and exit with code 1."""
    print(f"{Colors.RED}Error: {message}{Colors.NC}", file=sys.stderr)
    sys.exit(1)


def success(message: str) -> None:
    """Print success message."""
    print(f"{Colors.GREEN}{message}{Colors.NC}")


def warn(message: str) -> None:
    """Print warning message."""
    print(f"{Colors.YELLOW}Warning: {message}{Colors.NC}")


@dataclass
class APIResponse:
    """Wrapper for API response data."""
    data: Dict[str, Any]
    status_code: int


class VNDBClient:
    """HTTP client for VNDB API v2 (Kana)."""
    
    def __init__(self, base_url: str = API_BASE_URL, timeout: int = DEFAULT_TIMEOUT):
        """
        Initialize the VNDB API client.
        
        Args:
            base_url: The base URL for the VNDB API
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
    
    def _make_request(
        self, 
        endpoint: str, 
        method: str = "GET", 
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None
    ) -> APIResponse:
        """
        Make an HTTP request to the API.
        
        Args:
            endpoint: API endpoint path
            method: HTTP method (GET or POST)
            data: JSON payload for POST requests
            params: Query parameters for GET requests
            
        Returns:
            APIResponse containing parsed JSON data and status code
            
        Raises:
            SystemExit: On network or API errors
        """
        # Build URL with query parameters for GET requests
        url = f"{self.base_url}/{endpoint}"
        if params:
            url = f"{url}?{urlencode(params)}"
        
        # Prepare request
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        # Encode JSON data for POST requests
        encoded_data = None
        if data and method == "POST":
            encoded_data = json.dumps(data).encode('utf-8')
        
        request = urllib.request.Request(
            url,
            data=encoded_data,
            headers=headers,
            method=method
        )
        
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode('utf-8'))
                return APIResponse(data=response_data, status_code=response.getcode())
        
        except urllib.error.HTTPError as e:
            error(f"HTTP {e.code}: {e.reason}")
        except urllib.error.URLError as e:
            error(f"Network error: {e.reason}")
        except json.JSONDecodeError as e:
            error(f"Failed to parse JSON response: {e}")
        except TimeoutError:
            error(f"Request timed out after {self.timeout} seconds")
        
        # This line should never be reached due to error() calls above
        return APIResponse(data={}, status_code=0)
    
    def post(self, endpoint: str, data: Dict[str, Any]) -> APIResponse:
        """Send a POST request to the API."""
        return self._make_request(endpoint, method="POST", data=data)
    
    def get(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> APIResponse:
        """Send a GET request to the API."""
        return self._make_request(endpoint, method="GET", params=params)


def format_json(data: Dict[str, Any]) -> str:
    """
    Format JSON data with indentation for pretty printing.
    
    Args:
        data: Dictionary to format as JSON
        
    Returns:
        Pretty-printed JSON string
    """
    return json.dumps(data, indent=2, ensure_ascii=False)


def search_character(
    client: VNDBClient, 
    keyword: str, 
    fields: str = "name,original,image.url,description,vns.title",
    results: int = 20
) -> None:
    """
    Search for characters by keyword.
    
    Args:
        client: VNDB API client instance
        keyword: Search keyword
        fields: Comma-separated list of fields to return
        results: Maximum number of results
    """
    print(f"Searching characters: {keyword}...")
    
    payload = {
        "filters": ["search", "=", keyword],
        "fields": fields,
        "results": results
    }
    
    response = client.post("character", payload)
    print(format_json(response.data))


def search_vn(
    client: VNDBClient,
    keyword: str,
    fields: str = "title,alttitle,image.url,rating,released,developers.name",
    results: int = 10
) -> None:
    """
    Search for visual novels by keyword.
    
    Args:
        client: VNDB API client instance
        keyword: Search keyword
        fields: Comma-separated list of fields to return
        results: Maximum number of results
    """
    print(f"Searching visual novels: {keyword}...")
    
    payload = {
        "filters": ["search", "=", keyword],
        "fields": fields,
        "results": results
    }
    
    response = client.post("vn", payload)
    print(format_json(response.data))


def get_vn_by_id(
    client: VNDBClient,
    vn_id: str,
    fields: str = "title,alttitle,image.url,rating,description,released,developers.name"
) -> None:
    """
    Get a visual novel by its ID.
    
    Args:
        client: VNDB API client instance
        vn_id: VN ID (e.g., "v17")
        fields: Comma-separated list of fields to return
    """
    print(f"Querying VN ID: {vn_id}...")
    
    payload = {
        "filters": ["id", "=", vn_id],
        "fields": fields
    }
    
    response = client.post("vn", payload)
    print(format_json(response.data))


def get_latest_vn(
    client: VNDBClient,
    results: int = 5,
    fields: str = "title,alttitle,released,developers.name,rating,votecount"
) -> None:
    """
    Get the latest released visual novels.
    
    Args:
        client: VNDB API client instance
        results: Maximum number of results
        fields: Comma-separated list of fields to return
    """
    print(f"Fetching latest {results} releases...")
    
    payload = {
        "filters": ["released", ">=", "2024-01-01"],
        "fields": fields,
        "sort": "released",
        "reverse": True,
        "results": results
    }
    
    response = client.post("vn", payload)
    print(format_json(response.data))


def get_stats(client: VNDBClient) -> None:
    """
    Get database statistics.
    
    Args:
        client: VNDB API client instance
    """
    print("Fetching database statistics...")
    response = client.get("stats")
    print(format_json(response.data))


def get_user(
    client: VNDBClient,
    username: str,
    fields: str = "lengthvotes,lengthvotes_sum"
) -> None:
    """
    Get user information.
    
    Args:
        client: VNDB API client instance
        username: Username to query
        fields: Comma-separated list of fields to return
    """
    print(f"Querying user: {username}...")
    
    params = {"q": username, "fields": fields}
    response = client.get("user", params)
    print(format_json(response.data))


def get_schema(client: VNDBClient) -> None:
    """
    Get API schema information.
    
    Args:
        client: VNDB API client instance
    """
    print("Fetching API schema...")
    response = client.get("schema")
    print(format_json(response.data))


def query_endpoint(
    client: VNDBClient,
    endpoint: str,
    filters: str,
    fields: str,
    sort: str = "id",
    results: int = 10
) -> None:
    """
    Generic query for any API endpoint.
    
    Args:
        client: VNDB API client instance
        endpoint: API endpoint name
        filters: Filter specification (JSON string or key:value)
        fields: Comma-separated list of fields to return
        sort: Field to sort by
        results: Maximum number of results
    """
    # Parse filter string into proper format
    if "[" in filters:
        # Already in JSON format
        try:
            filter_json = json.loads(filters)
        except json.JSONDecodeError:
            error(f"Invalid filter JSON: {filters}")
            return
    else:
        # Simple key:value format
        if ":" not in filters:
            error(f"Invalid filter format. Use 'key:value' or JSON array")
            return
        key, val = filters.split(":", 1)
        filter_json = [key.strip(), "=", val.strip()]
    
    payload = {
        "filters": filter_json,
        "fields": fields,
        "sort": sort,
        "results": results
    }
    
    print(f"Querying endpoint: {endpoint}")
    print(f"Request: {format_json(payload)}")
    
    response = client.post(endpoint, payload)
    print(format_json(response.data))


def show_help() -> None:
    """Display help message with usage instructions."""
    help_text = """
VNDB API Query Tool - Python Version
Lightweight VNDB API v2 (Kana) query script

Usage:
  python vndb_query.py <command> [arguments]

Quick Commands:
  character <keyword> [fields] [count]     Search characters
  vn <keyword> [fields] [count]            Search visual novels
  vn_id <ID> [fields]                      Get VN by ID
  latest [count] [fields]                  Latest releases
  stats                                    Database statistics
  user <username> [fields]                 Query user
  schema                                   Get API Schema

Advanced Commands:
  query <endpoint> <filters> <fields> [sort] [results]
                                           Generic query

Examples:
  python vndb_query.py character "美雪"
  python vndb_query.py vn "Steins;Gate"
  python vndb_query.py vn_id "v17"
  python vndb_query.py latest 5
  python vndb_query.py stats
  python vndb_query.py user "yorhel"

Field Format (comma-separated):
  Common character fields: name,original,image.url,description,vns.title
  Common VN fields: title,alttitle,image.url,rating,released,developers.name

Notes:
  - Requires Python 3.7+
  - No external dependencies (uses standard library only)
  - API limit: 200 requests per 5 minutes
"""
    print(help_text)


def main() -> None:
    """Main entry point for the CLI application."""
    # Check Python version
    if sys.version_info < (3, 7):
        error("Python 3.7 or higher is required")
    
    # Check command line arguments
    if len(sys.argv) < 2:
        show_help()
        sys.exit(0)
    
    command = sys.argv[1]
    args = sys.argv[2:]
    
    # Initialize API client
    client = VNDBClient()
    
    # Route commands to appropriate handlers
    if command == "character":
        if len(args) < 1:
            error("Usage: python vndb_query.py character <keyword> [fields] [count]")
        search_character(client, args[0], args[1] if len(args) > 1 else "name,original,image.url,description,vns.title",
                        int(args[2]) if len(args) > 2 else 20)
    
    elif command == "vn":
        if len(args) < 1:
            error("Usage: python vndb_query.py vn <keyword> [fields] [count]")
        search_vn(client, args[0], args[1] if len(args) > 1 else "title,alttitle,image.url,rating,released,developers.name",
                 int(args[2]) if len(args) > 2 else 10)
    
    elif command == "vn_id":
        if len(args) < 1:
            error("Usage: python vndb_query.py vn_id <ID> [fields]")
        get_vn_by_id(client, args[0], args[1] if len(args) > 1 else "title,alttitle,image.url,rating,description,released,developers.name")
    
    elif command == "latest":
        get_latest_vn(client, int(args[0]) if len(args) > 0 else 5,
                     args[1] if len(args) > 1 else "title,alttitle,released,developers.name,rating,votecount")
    
    elif command == "stats":
        get_stats(client)
    
    elif command == "user":
        if len(args) < 1:
            error("Usage: python vndb_query.py user <username> [fields]")
        get_user(client, args[0], args[1] if len(args) > 1 else "lengthvotes,lengthvotes_sum")
    
    elif command == "schema":
        get_schema(client)
    
    elif command == "query":
        if len(args) < 3:
            error("Usage: python vndb_query.py query <endpoint> <filters> <fields> [sort] [results]")
        query_endpoint(client, args[0], args[1], args[2],
                      args[3] if len(args) > 3 else "id",
                      int(args[4]) if len(args) > 4 else 10)
    
    elif command in ("help", "--help", "-h"):
        show_help()
    
    else:
        error(f"Unknown command: {command}. Use 'python vndb_query.py help' for usage information")


if __name__ == "__main__":
    main()

