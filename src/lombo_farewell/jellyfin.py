"""A small Jellyfin client, standard library only.

Authentication uses an API key (Jellyfin: Dashboard > API Keys) in the Authorization header.
Every failure becomes a JellyfinError with a short message, so callers print it instead of crashing.
The only writes are collection calls: create a collection, add titles to it, take titles out of it.
Nothing here can delete a title or a file; taking a title out of a collection only unlists it there.
"""

from typing import Any, cast
from urllib.parse import urlencode

from . import httpjson
from .core import norm_id

BATCH = 50  # ids per request, so a long list never exceeds the request-line limit
TITLE_TYPES = "Movie,Series"


class JellyfinError(Exception):
    """A request failed. The message names the path and the reason."""


class Jellyfin:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str, params: dict[str, str] | None = None, auth: bool = True) -> Any:
        url = self.base + path + (("?" + urlencode(params)) if params else "")
        headers = {"Authorization": f'MediaBrowser Token="{self.key}"'} if auth else {}
        try:
            return httpjson.request(method, url, path, headers, timeout=self.timeout)
        except httpjson.HttpError as e:
            raise JellyfinError(str(e)) from e

    def _items(self, params: dict[str, str]) -> list[dict[str, Any]]:
        data = self._call("GET", "/Items", params)
        items = data.get("Items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise JellyfinError("/Items: unexpected reply")
        return [i for i in items if isinstance(i, dict) and isinstance(i.get("Id"), str)]

    def public_info(self) -> dict[str, Any]:
        """Server name and version. Needs no key, so it tells 'unreachable' apart from 'bad key'."""
        return cast(dict[str, Any], self._call("GET", "/System/Info/Public", auth=False))

    def info(self) -> dict[str, Any]:
        """Needs a valid key."""
        return cast(dict[str, Any], self._call("GET", "/System/Info"))

    def find_collection(self, name: str) -> str | None:
        """The id of the collection with exactly this name, or None."""
        found = [
            i["Id"]
            for i in self._items({"IncludeItemTypes": "BoxSet", "Recursive": "true", "SearchTerm": name})
            if i.get("Name") == name
        ]
        return found[0] if found else None

    def collection_members(self, collection_id: str) -> set[str]:
        """Ids (bare, lower case) of the movies and shows in a collection."""
        items = self._items(
            {
                "ParentId": collection_id,
                "Recursive": "true",
                "IncludeItemTypes": TITLE_TYPES,
                "CollapseBoxSetItems": "false",
            }
        )
        return {norm_id(i["Id"]) for i in items}

    def create_collection(self, name: str, ids: list[str]) -> str:
        """Make a locked collection holding the first batch of ids; add the rest in further calls."""
        params = {"Name": name, "IsLocked": "true"}
        if ids:
            params["Ids"] = ",".join(ids[:BATCH])
        made = self._call("POST", "/Collections", params)
        new_id = made.get("Id") if isinstance(made, dict) else None
        coll = new_id if isinstance(new_id, str) else self.find_collection(name)
        if coll is None:
            raise JellyfinError("/Collections: the new collection cannot be found")
        self.add_to_collection(coll, ids[BATCH:])
        return coll

    def add_to_collection(self, collection_id: str, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self._call("POST", f"/Collections/{collection_id}/Items", {"ids": ",".join(ids[i : i + BATCH])})

    def remove_from_collection(self, collection_id: str, ids: list[str]) -> None:
        for i in range(0, len(ids), BATCH):
            self._call("DELETE", f"/Collections/{collection_id}/Items", {"ids": ",".join(ids[i : i + BATCH])})

    def names(self, ids: list[str]) -> dict[str, str]:
        """Titles for ids, in batches. An id Jellyfin does not know is simply missing from the answer."""
        out: dict[str, str] = {}
        for i in range(0, len(ids), BATCH):
            for item in self._items({"Ids": ",".join(ids[i : i + BATCH]), "Recursive": "true"}):
                if isinstance(item.get("Name"), str):
                    out[norm_id(item["Id"])] = item["Name"]
        return out

    def ids_named(self, title: str) -> list[str]:
        """Ids of movies and shows whose name is exactly this (ignoring case)."""
        items = self._items({"IncludeItemTypes": TITLE_TYPES, "Recursive": "true", "SearchTerm": title})
        return [norm_id(i["Id"]) for i in items if str(i.get("Name", "")).lower() == title.lower()]
