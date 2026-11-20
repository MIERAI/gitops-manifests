"""Image tags in the manifests of an environment, for set-image.sh / promote.sh. Where they are:

  <app>/app.yaml       the component that deploys the image says (scripts/appdesc.py), else by convention:
  kustomize            <app>/overlays/<env>/kustomization.yaml   images: [{name: <image>, newName?: <image>, newTag: <tag>}]
  helm                 <app>/values-<env>.yaml                   any mapping {repository: <image>, tag: <tag>} (the Helm
                                                                 chart convention; several images = several mappings)

An image is ghcr.io/<owner>/<name>. Writing an image the file does not have yet, but whose <name> it has once (a fork:
ghcr.io/<you>/sample-app for ghcr.io/mierai/sample-app), switches that entry to it: kustomize newName, Helm repository.
Environments not switched yet keep running the upstream image; promotions carry the switch along.

Edits change only the lines concerned (comments and layout stay): the files are edited as text, not re-serialized.
"""
import os
import re
import sys

def base(image):
    return image.rsplit("/", 1)[-1]


def env_file(app, env, image=None):
    """The file that holds the image tags of <app> in <env> (image: which one, when the app has several components)."""
    if os.path.exists(f"{app}/app.yaml"):
        import appdesc
        d = appdesc.load(app)
        if env not in [e["name"] for e in d["environments"]]:
            sys.exit(f"{app}/app.yaml: no environment {env}")
        return appdesc.component_file(d, appdesc.component_of(d, image), env)
    k = f"{app}/overlays/{env}/kustomization.yaml"
    h = f"{app}/values-{env}.yaml"
    if os.path.exists(k):
        return k
    if os.path.exists(h):
        return h
    sys.exit(f"{app}: neither {k} nor {h}")


KEY = re.compile(r"^(\s*(?:- )?)([\w.-]+):\s*\"?([^\"\s#]*)\"?")


def _helm_entries(lines):
    """[(image, tag, index of the tag line, index of the repository line)] for every {repository, tag} mapping."""
    out = []
    for i, line in enumerate(lines):
        m = KEY.match(line)
        if not m or m.group(2) != "repository":
            continue
        col, image, tag = len(m.group(1)), m.group(3), None
        ranges = [range(i + 1, len(lines))]  # siblings below, then above (unless repository starts a list item)
        if "- " not in m.group(1):
            ranges.append(range(i - 1, -1, -1))
        for rng in ranges:
            for j in rng:
                s = lines[j]
                if not s.strip() or s.lstrip().startswith("#"):
                    continue
                k = KEY.match(s)
                kcol = len(k.group(1)) if k else len(s) - len(s.lstrip())
                item = s.lstrip().startswith("- ")
                if kcol < col or (item and kcol <= col and j > i):
                    break  # left the mapping (or the next list item)
                if kcol == col and k and k.group(2) == "tag":
                    tag = (k.group(3), j)
                    break
                if item and kcol == col:
                    break  # first line of this list item: nothing above belongs to it
            if tag:
                break
        if tag:
            out.append((image, tag[0], tag[1], i))
    return out


def _kustomize_entries(lines):
    """[{name, newName, newTag, lines: {key: index}, indent}] for the entries of images:."""
    out, cur, in_images = [], None, False
    for i, line in enumerate(lines):
        if re.match(r"^images:\s*$", line):
            in_images = True
            continue
        if in_images and line.strip() and not line.startswith(" ") and not line.startswith("-"):
            in_images = False  # the next top-level key
        if not in_images:
            continue
        m = re.match(r"^(\s*)- name:\s*\"?([^\"\s#]+)\"?", line)
        if m:
            cur = {"name": m.group(2), "newName": None, "newTag": None, "lines": {"name": i}, "indent": len(m.group(1)) + 2}
            out.append(cur)
            continue
        k = re.match(r"^(\s+)(newName|newTag):\s*\"?([^\"\s#]*)\"?", line)
        if cur and k and len(k.group(1)) == cur["indent"]:
            cur[k.group(2)] = k.group(3)
            cur["lines"][k.group(2)] = i
    return out


def read(path, text=None):
    """[(image, tag)] in file order, image = what runs (newName when set) (text: the content, e.g. at an older commit;
    default: the file)."""
    text = open(path).read() if text is None else text
    if path.endswith("kustomization.yaml"):
        return [(e["newName"] or e["name"], e["newTag"]) for e in _kustomize_entries(text.split("\n")) if e["newTag"]]
    return [(img, tag) for img, tag, _, _ in _helm_entries(text.split("\n"))]


def _pick(entries, image, effective):
    """The one entry for image: running it, else the only one with its <name> (then it switches to image)."""
    exact = [e for e in entries if effective(e) == image]
    if len(exact) == 1:
        return exact[0], False
    same = [e for e in entries if base(effective(e)) == base(image)]
    if not exact and len(same) == 1:
        return same[0], True
    return None, False


def write(path, image, tag):
    """Set image (and its tag) in path."""
    lines = open(path).read().split("\n")
    if path.endswith("kustomization.yaml"):
        e, switch = _pick(_kustomize_entries(lines), image, lambda e: e["newName"] or e["name"])
        if e is None:
            sys.exit(f"{path}: no single entry for image {image}")
        pad = " " * e["indent"]
        if "newTag" in e["lines"]:
            lines[e["lines"]["newTag"]] = f'{pad}newTag: "{tag}"'
        else:
            lines.insert(e["lines"]["name"] + 1, f'{pad}newTag: "{tag}"')
        if switch:
            if "newName" in e["lines"]:
                lines[e["lines"]["newName"]] = f"{pad}newName: {image}"
            else:
                lines.insert(e["lines"]["name"] + 1, f"{pad}newName: {image}")
    else:
        hits = _helm_entries(lines)
        exact = [h for h in hits if h[0] == image]
        same = [h for h in hits if base(h[0]) == base(image)]
        pick = exact if len(exact) == 1 else (same if not exact and len(same) == 1 else [])
        if not pick:
            sys.exit(f"{path}: no single entry for image {image}")
        img, _, i, r = pick[0]
        lines[i] = re.sub(r"(tag:\s*)\"?[^\"\s#]*\"?", rf'\g<1>"{tag}"', lines[i], count=1)
        if img != image:  # switch the repository of that mapping
            lines[r] = re.sub(r"(repository:\s*)\"?[^\"\s#]+\"?", rf"\g<1>{image}", lines[r], count=1)
    open(path, "w").write("\n".join(lines))


if __name__ == "__main__":  # python3 scripts/images.py <file>: print image:tag per line
    for img, tag in read(sys.argv[1]):
        print(f"{img}:{tag}")
