"""Where the code lives, in one place: the site, the agent card and the MCP server all link here."""

REPO = "https://github.com/NadyKor15/billradar-nemotron"
BRANCH = "main"


def tree() -> str:
    return f"{REPO}/tree/{BRANCH}"


def blob(path: str) -> str:
    return f"{REPO}/blob/{BRANCH}/{path}"
