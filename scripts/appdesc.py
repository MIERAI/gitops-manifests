"""Application descriptors (<app>/app.yaml): the one place an application is registered (format: storefront/app.yaml).
The ApplicationSet (apps/applications.yaml) derives the Argo CD Applications from them; the scripts derive the same
here — which file holds an image in an environment, and how to render each Application.

Needs PyYAML (CI: the python3-yaml package; locally: `uv run --with pyyaml …` or pip).
"""
import glob
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required (apt install python3-yaml, or pip install pyyaml)")


def load(app, root="."):
    """The descriptor of directory <app>, or None (an application not migrated yet)."""
    f = os.path.join(root, app, "app.yaml")
    if not os.path.exists(f):
        return None
    d = yaml.safe_load(open(f))
    d["dir"] = app
    return d


def all_apps(root="."):
    return [load(os.path.basename(os.path.dirname(f)), root) for f in sorted(glob.glob(os.path.join(root, "*", "app.yaml")))]


def component_of(d, image):
    """The component that deploys image: by the services list, else the only component (which then also holds the
    images that are not a service of their own, e.g. api-factory's migration image next to its API)."""
    if image:  # the service's image, or a fork's (same <name>: ghcr.io/<you>/<name>)
        hits = [s for s in d.get("services", []) if s["image"] == image] or \
               [s for s in d.get("services", []) if s["image"].rsplit("/", 1)[-1] == image.rsplit("/", 1)[-1]]
        if len(hits) == 1:
            return next(c for c in d["components"] if c["name"] == hits[0]["component"])
    if len(d["components"]) == 1:
        return d["components"][0]
    if image:
        sys.exit(f"{d['dir']}/app.yaml: no service with image {image}")
    sys.exit(f"{d['dir']}/app.yaml: several components, name the image")


def component_file(d, c, env):
    """The file of component c that holds the image tags in env."""
    if "helm" in c:
        return f"{d['dir']}/{c['helm']}/values-{env}.yaml"
    return f"{d['dir']}/{c['kustomize']}/{env}/kustomization.yaml"


def applications(d, root="."):
    """What the ApplicationSet makes of descriptor d (keep in step with apps/applications.yaml)."""
    out = []
    for c in d["components"]:
        for e in d["environments"]:
            env = e["name"]
            if "helm" in c:
                path, values = f"{d['dir']}/{c['helm']}/chart", [f"../values-{env}.yaml"]
            else:
                path, values = f"{d['dir']}/{c['kustomize']}/{env}", []
            out.append({"name": f"{c['name']}-{env}", "path": path, "namespace": f"{d['name']}-{env}", "values": values,
                        "release": c["name"], "enforced": True})
    return out
