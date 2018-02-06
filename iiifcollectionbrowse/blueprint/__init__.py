"""
iiifcollectionbrowse
"""
import logging
from threading import Thread

from flask import Blueprint, render_template, url_for, request

import requests

from .utils import IIIFUrl


__author__ = "Brian Balsamo"
__email__ = "brian@brianbalsamo.com"
__version__ = "0.0.1"


log = logging.getLogger(__name__)


BLUEPRINT = Blueprint('iiifcollectionbrowse', __name__,
                      template_folder='templates',
                      static_folder='static')


VIEWER_URL = "https://iiif-viewer.lib.uchicago.edu/uv/uv.html#"
REQUESTS_TIMEOUT = 1/10
NO_THUMB_IMG_URL = ""
COLORS = {
    "contrast_color": "#800000",
    "thumbnail_backdrop": "#D6D6CE"
}


def get_thumbnail(rec, width=200, height=200, preserve_ratio=True):
    if preserve_ratio:
        width = "!"+str(width)
    # If we pass an identifier just try and
    # get the record from the identifier.
    updated = False
    if not isinstance(rec, dict):
        try:
            resp = requests.get(rec, timeout=REQUESTS_TIMEOUT)
            resp.raise_for_status()
            rec = resp.json()
            updated = True
        except Exception:
            # TODO: handle failure?
            raise
    # Try and update the record from its URI, if we didn't
    # already just download it.
    try:
        if not updated:
            remote_rec_resp = requests.get(rec['@id'], timeout=REQUESTS_TIMEOUT)
            remote_rec_resp.raise_for_status()
            remote_rec_json = remote_rec_resp.json()
            rec.update(remote_rec_json)
    except Exception:
        # Lets hope we have a complete record on our hands, instead
        # of a stub with just identifiers in it.
        pass
    # If one is hardcoded
    if rec.get('thumbnail'):
        # If the thumbnail claims the IIIF Image API Service
        if rec['thumbnail'].get("service") and \
                rec['thumbnail']['service'].get("@context") in  \
                ["http://iiif.io/api/image/2/context.json"]:
            u = IIIFUrl.from_url(rec['thumbnail']['@id'])
            u.size = "{},{}".format(width, height)
            return u.to_image_url()
        # Otherwise it's just a link
        if rec['thumbnail'].get("@id"):
            return rec['thumbnail']['@id']
    # Dynamic functionality - get the first image that should be relevant
    if rec['@type'] == "sc:Collection":
        # prefer the first member, if it exists
        if rec.get("members"):
            return get_thumbnail(rec['members'][0])
        # otherwise try for manifests
        elif rec.get("manifests"):
            return get_thumbnail(rec['manifests'][0])
        # finally check for subcollections
        elif rec.get("collections"):
            return get_thumbnail(rec['collections'][0])
        else:
            raise ValueError()
    elif rec['@type'] == "sc:Manifest":
        # sequences MUST be > 0
        return get_thumbnail(rec['sequences'][0])
    elif rec['@type'] == "sc:Sequence":
        # canvases MUST be > 0
        return get_thumbnail(rec['canvases'][0])
    elif rec['@type'] == "sc:Canvas":
        if rec.get('images'):
            return get_thumbnail(rec['images'][0])
        else:
            raise ValueError()
    # We made it!
    elif rec['@type'] == "oa:Annotation":
        # Be sure we haven't stumbled into something
        # that isn't an image
        if rec.get("resource") is None:
            raise ValueError()
        # TODO: Actually parse the URL
        # Time for a hack for just the moment
        x = rec['resource']['@id']
        print(x)
        u = IIIFUrl.from_url(x)
        u.size = "{},{}".format(width, height)
        print(u.to_image_url())
        return u.to_image_url()
    else:
        raise ValueError()


def threaded_thumbnails(identifier, result, index):
    # Wrap up this operation so we can use this function
    # as the target of a threading.Thread
    result[index] = get_thumbnail(identifier)


@BLUEPRINT.route("/<path:c_url>")
def collection(c_url):
    # Try to pull the collection record
    resp = requests.get(c_url, timeout=REQUESTS_TIMEOUT)
    resp.raise_for_status()
    rj = resp.json()
    # Parse the record
    members = []
    collections = []
    manifests = []
    if rj.get("members"):
        members = rj['members']
    if rj.get('collections'):
        collections = rj['collections']
    if rj.get('manifests'):
        manifests = rj['manifests']
    # build template urls
    for x in collections:
        x['t_url'] = url_for(".collection", c_url=x['@id'])
    for x in members:
        if x['@type'] == "sc:Collection":
            x['t_url'] = url_for(".collection", c_url=x['@id'])
    # Get if the current request is paginated or not
    page = request.args.get("page", 1)
    try:
        page = int(page)
    except Exception:
        page = 1
    # Thumbnail view - paginated
    if rj.get('viewingHint') == "individuals" and page > 0:
        # 30 results per page max, to not block too long on
        # dynamically generating thumbnails even in bad cases
        total = max(len(members), len(collections), len(manifests))
        start = (page-1)*10
        end = page*10
        # Generate sublists of just the stuff for this page
        members = members[start:end]
        collections = collections[start:end]
        manifests = manifests[start:end]
        # Assemble page links and stuff
        list_view = "{}?page=-1".format(url_for(".collection", c_url=c_url))
        if end > total:
            next_page = None
        else:
            next_page = "{}?page={}".format(url_for(".collection", c_url=c_url), str(page+1))
        prev_page = None
        if page > 1:
            prev_page = "{}?page={}".format(url_for(".collection", c_url=c_url), str(page-1))
        # Handle thumbnail finding for viewingHint == individuals
        # https://stackoverflow.com/questions/6893968/
        # how-to-get-the-return-value-from-a-thread-in-python
        # Note: This makes the order the lists are concat'd together
        # in important - otherwise thumbnails won't match.
        results = [None] * len(members+collections+manifests)
        for i, x in enumerate(members + collections + manifests):
            x['thumb_thread'] = Thread(target=threaded_thumbnails, args=(x['@id'], results, i))
            x['thumb_thread'].start()
        for i, x in enumerate(members + collections + manifests):
            x['thumb_thread'].join(timeout=10)
            if results[i] is not None:
                x['thumb_url'] = results[i]
            else:
                x['thumb_url'] = NO_THUMB_IMG_URL
        return render_template(
            "collection_thumbnails.html",
            viewer_url=VIEWER_URL,
            cname=rj['label'],
            cdesc=rj.get('description'),
            list_view=list_view,
            next_page=next_page,
            prev_page=prev_page,
            members=members,
            collections=collections,
            manifests=manifests,
            colors=COLORS
        )
    # List view - no pagination
    else:
        thumbnail_view = None
        if rj.get('viewingHint') == 'individuals':
            thumbnail_view = "{}?page=1".format(url_for(".collection", c_url=c_url))
        return render_template(
            "collection_list.html",
            viewer_url=VIEWER_URL,
            cname=rj['label'],
            cdesc=rj.get('description'),
            members=members,
            collections=collections,
            manifests=manifests,
            colors=COLORS,
            thumbnail_view=thumbnail_view
        )
