import json
import collect

request = collect.youtube_request
options = {}
sets = {}
meta = json.loads((collect.ROOT / "data/games.json").read_text())
active = {gid for gid,v in meta.items() if not v.get("removed")}
for mode in ("A to Z", "Z to A", "Latest", "Oldest"):
    pages = []
    def traced(url, body=None):
        if body and "browseId" in body and mode != "A to Z":
            body = dict(body, params=options[mode])
        raw = request(url, body)
        if body:
            nodes = list(collect.objects(json.loads(raw)))
            cards = [v["miniGameCardViewModel"]["title"] for v in nodes if "miniGameCardViewModel" in v]
            for v in nodes:
                item = v.get("listItemViewModel", {})
                for cmd in collect.objects(item):
                    ep = cmd.get("browseEndpoint", {})
                    if ep.get("params") and ep.get("browseId") == "FEmini_app_destination":
                        options[item["title"]["content"]] = ep["params"]
            pages.append((len(cards), cards[:1], cards[-1:], sum("continuationCommand" in v for v in nodes)))
        return raw
    collect.youtube_request = traced
    ids = {g["id"] for g in collect.fetch_youtube_catalog()}
    sets[mode] = ids
    print("RESULT", mode, len(ids), "pages",len(pages),"first",pages[0],"last",pages[-1],"new_vs_AZ",len(ids-sets["A to Z"]),flush=True)
union = set.union(*sets.values())
print("RESULT union",len(union),"active matched",len(active & union),"of",len(active))
