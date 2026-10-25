"""Run with python -m github_auth_check after setting GitHub environment variables."""

import json
import sys

from github_client import GitHubClient, GitHubConfig, GitHubError


def main():
    try:
        result = GitHubClient(GitHubConfig.from_environment()).verify_access()
    except GitHubError as error:
        print(json.dumps({"status": "error", "code": error.code, "message": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
