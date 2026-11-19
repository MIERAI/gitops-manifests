"""Image tags in the manifests of an environment, for set-image.sh / promote.sh. Where they are:

  <app>/app.yaml       the component that deploys the image says (scripts/appdesc.py), else by convention:
  kustomize            <app>/overlays/<env>/kustomization.yaml   images: [{name: <image>, newTag: <tag>}]
  helm                 <app>/values-<env>.yaml                   any mapping {repository: <image>, tag: <tag>} (the Helm
                                                                 chart convention; several images = several mappings)

Edits change only the tag's line (comments and layout stay): the files are edited as text, not re-serialized.
"""
import os
import re
import sys

KUSTOMIZE = re.compile(r"- name: (\S+)\n\s+newTag: \"?([^\"\s]+)\"?")


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
    """[(image, tag, index of the tag line)] for every {repository, tag} mapping."""
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
            out.append((image, tag[0], tag[1]))
    return out


def read(path, text=None):
    """[(image, tag)] in file order (text: the content, e.g. at an older commit; default: the file)."""
    text = open(path).read() if text is None else text
    if path.endswith("kustomization.yaml"):
        return KUSTOMIZE.findall(text)
    return [(img, tag) for img, tag, _ in _helm_entries(text.split("\n"))]


def write(path, image, tag):
    """Set the tag of image in path (exactly one occurrence)."""
    text = open(path).read()
    if path.endswith("kustomization.yaml"):
        text, n = re.subn(rf"(- name: {re.escape(image)}\n\s+newTag: )\S+", rf'\g<1>"{tag}"', text)
    else:
        lines = text.split("\n")
        hits = [i for img, _, i in _helm_entries(lines) if img == image]
        n = len(hits)
        if n == 1:
            i = hits[0]
            lines[i] = re.sub(r"(tag:\s*)\"?[^\"\s#]*\"?", rf'\g<1>"{tag}"', lines[i], count=1)
            text = "\n".join(lines)
    if n != 1:
        sys.exit(f"{path}: image {image} found {n} times (expected once)")
    open(path, "w").write(text)


if __name__ == "__main__":  # python3 scripts/images.py <file>: print image:tag per line
    for img, tag in read(sys.argv[1]):
        print(f"{img}:{tag}")
