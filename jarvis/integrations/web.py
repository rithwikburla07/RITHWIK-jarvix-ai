"""Browser handoffs for explicitly parsed URLs and search commands."""
from urllib.parse import urlencode

from ..core.intents import Reply
from ..workflows.loader import validate_url, WorkflowError


def search_url(query, provider="google"):
    query = query.strip()
    if not 1 <= len(query) <= 500 or any(ord(char) < 32 for char in query):
        raise ValueError("Use a search query between 1 and 500 characters.")
    if provider == "youtube":
        return "https://www.youtube.com/results?" + urlencode({"search_query": query})
    return "https://www.google.com/search?" + urlencode({"q": query})


class WebActions:
    def __init__(self, open_url, open_folder):
        self.open_url, self.open_folder = open_url, open_folder

    def handle(self, intent, _now):
        try:
            if intent.name == "open_folder":
                opened = self.open_folder(intent.get("folder", ""))
            else:
                url = (search_url(intent.get("query", ""), "youtube" if intent.name == "youtube_search" else "google")
                       if intent.name in {"web_search", "youtube_search"}
                       else validate_url(intent.get("url", "")))
                opened = self.open_url(url)
        except (ValueError, WorkflowError):
            return Reply("refused", "Enter a valid HTTP(S) address or a short search query.", "web.invalid"), None
        except OSError:
            opened = False
        return (Reply("done", "Opened the destination.", "web.opened") if opened else
                Reply("unavailable", "The destination could not be opened on this PC.", "web.unavailable")), None
