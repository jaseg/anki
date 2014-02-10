# -*- coding: utf-8 -*-
# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import urllib.request, urllib.parse, urllib.error
import os
import sys
import gzip
import random
from io import StringIO

from anki.db import DB
from anki.utils import ids2str, intTime, json, isWin, isMac, platDesc, checksum
from anki.consts import *
from .hooks import runHook
import anki
import requests
from requests.exceptions import HTTPError

# syncing vars
HTTP_TIMEOUT = 90
HTTP_PROXY = None

CERT_PATH = os.path.join(os.path.dirname(__file__), "ankiweb.certs")
if not os.path.exists(CERT_PATH):
    if isWin:
        CERT_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "ankiweb.certs")
    elif isMac:
        CERT_PATH = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "../Resources/ankiweb.certs")
    else:
        assert 0, "Your distro has not packaged Anki correctly."

# Incremental syncing
##########################################################################

class Syncer(object):

    def __init__(self, col, server=None):
        self.col = col
        self.server = server

    def sync(self):
        "Returns 'noChanges', 'fullSync', 'success', etc"
        self.syncMsg = ""
        self.uname = ""
        # if the deck has any pending changes, flush them first and bump mod
        # time
        self.col.save()
        # step 1: login & metadata
        runHook("sync", "login")
        meta = self.server.meta()
        self.col.log("rmeta", meta)
        if not meta:
            return "badAuth"
        rscm = meta['scm']
        rts = meta['ts']
        self.rmod = meta['mod']
        self.maxUsn = meta['usn']
        self.mediaUsn = meta['musn']
        self.syncMsg = meta['msg']
        # this is a temporary measure to address the problem of users
        # forgetting which email address they've used - it will be removed
        # when enough time has passed
        self.uname = meta.get("uname", "")
        # server requested abort?
        if not meta['cont']:
            return "serverAbort"
        else:
            # don't abort, but ui should show message after sync finishes
            # and require confirmation if it's non-empty
            pass
        meta = self.meta()
        self.col.log("lmeta", meta)
        self.lmod = meta['mod']
        self.minUsn = meta['usn']
        lscm = meta['scm']
        lts = meta['ts']
        if abs(rts - lts) > 300:
            self.col.log("clock off")
            return "clockOff"
        if self.lmod == self.rmod:
            self.col.log("no changes")
            return "noChanges"
        elif lscm != rscm:
            self.col.log("schema diff")
            return "fullSync"
        self.lnewer = self.lmod > self.rmod
        # step 1.5: check collection is valid
        if not self.col.basicCheck():
            self.col.log("basic check")
            return "basicCheckFailed"
        # step 2: deletions
        runHook("sync", "meta")
        lrem = self.removed()
        rrem = self.server.start(
            minUsn=self.minUsn, lnewer=self.lnewer, graves=lrem)
        self.remove(rrem)
        # ...and small objects
        lchg = self.changes()
        rchg = self.server.applyChanges(changes=lchg)
        self.mergeChanges(lchg, rchg)
        # step 3: stream large tables from server
        runHook("sync", "server")
        while 1:
            runHook("sync", "stream")
            chunk = self.server.chunk()
            self.col.log("server chunk", chunk)
            self.applyChunk(chunk=chunk)
            if chunk['done']:
                break
        # step 4: stream to server
        runHook("sync", "client")
        while 1:
            runHook("sync", "stream")
            chunk = self.chunk()
            self.col.log("client chunk", chunk)
            self.server.applyChunk(chunk=chunk)
            if chunk['done']:
                break
        # step 5: sanity check
        runHook("sync", "sanity")
        c = self.sanityCheck()
        ret = self.server.sanityCheck2(client=c)
        if ret['status'] != "ok":
            # roll back and force full sync
            self.col.rollback()
            self.col.modSchema()
            self.col.save()
            return "sanityCheckFailed"
        # finalize
        runHook("sync", "finalize")
        mod = self.server.finish()
        self.finish(mod)
        return "success"

    def meta(self):
        return dict(
            mod=self.col.mod,
            scm=self.col.scm,
            usn=self.col._usn,
            ts=intTime(),
            musn=0,
            msg="",
            cont=True
        )

    def changes(self):
        "Bundle up small objects."
        d = dict(models=self.getModels(),
                 decks=self.getDecks(),
                 tags=self.getTags())
        if self.lnewer:
            d['conf'] = self.getConf()
            d['crt'] = self.col.crt
        return d

    def applyChanges(self, changes):
        self.rchg = changes
        lchg = self.changes()
        # merge our side before returning
        self.mergeChanges(lchg, self.rchg)
        return lchg

    def mergeChanges(self, lchg, rchg):
        # then the other objects
        self.mergeModels(rchg['models'])
        self.mergeDecks(rchg['decks'])
        self.mergeTags(rchg['tags'])
        if 'conf' in rchg:
            self.mergeConf(rchg['conf'])
        # this was left out of earlier betas
        if 'crt' in rchg:
            self.col.crt = rchg['crt']
        self.prepareToChunk()

    def sanityCheck(self):
        if not self.col.basicCheck():
            return "failed basic check"
        for t in "cards", "notes", "revlog", "graves":
            if self.col.db.scalar(
                "select count() from %s where usn = -1" % t):
                return "%s had usn = -1" % t
        for g in self.col.decks.all():
            if g['usn'] == -1:
                return "deck had usn = -1"
        for t, usn in self.col.tags.allItems():
            if usn == -1:
                return "tag had usn = -1"
        found = False
        for m in self.col.models.all():
            if self.col.server:
                # the web upgrade was mistakenly setting usn
                if m['usn'] < 0:
                    m['usn'] = 0
                    found = True
            else:
                if m['usn'] == -1:
                    return "model had usn = -1"
        if found:
            self.col.models.save()
        self.col.sched.reset()
        # check for missing parent decks
        self.col.sched.deckDueList()
        # return summary of deck
        return [
            list(self.col.sched.counts()),
            self.col.db.scalar("select count() from cards"),
            self.col.db.scalar("select count() from notes"),
            self.col.db.scalar("select count() from revlog"),
            self.col.db.scalar("select count() from graves"),
            len(self.col.models.all()),
            len(self.col.decks.all()),
            len(self.col.decks.allConf()),
        ]

    def sanityCheck2(self, client):
        server = self.sanityCheck()
        if client != server:
            return dict(status="bad", c=client, s=server)
        return dict(status="ok")

    def usnLim(self):
        if self.col.server:
            return "usn >= %d" % self.minUsn
        else:
            return "usn = -1"

    def finish(self, mod=None):
        if not mod:
            # server side; we decide new mod time
            mod = intTime(1000)
        self.col.ls = mod
        self.col._usn = self.maxUsn + 1
        # ensure we save the mod time even if no changes made
        self.col.db.mod = True
        self.col.save(mod=mod)
        return mod

    # Chunked syncing
    ##########################################################################

    def prepareToChunk(self):
        self.tablesLeft = ["revlog", "cards", "notes"]
        self.cursor = None

    def cursorForTable(self, table):
        lim = self.usnLim()
        x = self.col.db.execute
        d = (self.maxUsn, lim)
        if table == "revlog":
            return x("""
select id, cid, %d, ease, ivl, lastIvl, factor, time, type
from revlog where %s""" % d)
        elif table == "cards":
            return x("""
select id, nid, did, ord, mod, %d, type, queue, due, ivl, factor, reps,
lapses, left, odue, odid, flags, data from cards where %s""" % d)
        else:
            return x("""
select id, guid, mid, mod, %d, tags, flds, '', '', flags, data
from notes where %s""" % d)

    def chunk(self):
        buf = dict(done=False)
        lim = 2500
        while self.tablesLeft and lim:
            curTable = self.tablesLeft[0]
            if not self.cursor:
                self.cursor = self.cursorForTable(curTable)
            rows = self.cursor.fetchmany(lim)
            fetched = len(rows)
            if fetched != lim:
                # table is empty
                self.tablesLeft.pop(0)
                self.cursor = None
                # if we're the client, mark the objects as having been sent
                if not self.col.server:
                    self.col.db.execute(
                        "update %s set usn=? where usn=-1"%curTable,
                        self.maxUsn)
            buf[curTable] = rows
            lim -= fetched
        if not self.tablesLeft:
            buf['done'] = True
        return buf

    def applyChunk(self, chunk):
        if "revlog" in chunk:
            self.mergeRevlog(chunk['revlog'])
        if "cards" in chunk:
            self.mergeCards(chunk['cards'])
        if "notes" in chunk:
            self.mergeNotes(chunk['notes'])

    # Deletions
    ##########################################################################

    def removed(self):
        cards = []
        notes = []
        decks = []
        if self.col.server:
            curs = self.col.db.execute(
                "select oid, type from graves where usn >= ?", self.minUsn)
        else:
            curs = self.col.db.execute(
                "select oid, type from graves where usn = -1")
        for oid, type in curs:
            if type == REM_CARD:
                cards.append(oid)
            elif type == REM_NOTE:
                notes.append(oid)
            else:
                decks.append(oid)
        if not self.col.server:
            self.col.db.execute("update graves set usn=? where usn=-1",
                                 self.maxUsn)
        return dict(cards=cards, notes=notes, decks=decks)

    def start(self, minUsn, lnewer, graves):
        self.maxUsn = self.col._usn
        self.minUsn = minUsn
        self.lnewer = not lnewer
        lgraves = self.removed()
        self.remove(graves)
        return lgraves

    def remove(self, graves):
        # pretend to be the server so we don't set usn = -1
        wasServer = self.col.server
        self.col.server = True
        # notes first, so we don't end up with duplicate graves
        self.col._remNotes(graves['notes'])
        # then cards
        self.col.remCards(graves['cards'], notes=False)
        # and decks
        for oid in graves['decks']:
            self.col.decks.rem(oid, childrenToo=False)
        self.col.server = wasServer

    # Models
    ##########################################################################

    def getModels(self):
        if self.col.server:
            return [m for m in self.col.models.all() if m['usn'] >= self.minUsn]
        else:
            mods = [m for m in self.col.models.all() if m['usn'] == -1]
            for m in mods:
                m['usn'] = self.maxUsn
            self.col.models.save()
            return mods

    def mergeModels(self, rchg):
        for r in rchg:
            l = self.col.models.get(r['id'])
            # if missing locally or server is newer, update
            if not l or r['mod'] > l['mod']:
                self.col.models.update(r)

    # Decks
    ##########################################################################

    def getDecks(self):
        if self.col.server:
            return [
                [g for g in self.col.decks.all() if g['usn'] >= self.minUsn],
                [g for g in self.col.decks.allConf() if g['usn'] >= self.minUsn]
            ]
        else:
            decks = [g for g in self.col.decks.all() if g['usn'] == -1]
            for g in decks:
                g['usn'] = self.maxUsn
            dconf = [g for g in self.col.decks.allConf() if g['usn'] == -1]
            for g in dconf:
                g['usn'] = self.maxUsn
            self.col.decks.save()
            return [decks, dconf]

    def mergeDecks(self, rchg):
        for r in rchg[0]:
            l = self.col.decks.get(r['id'], False)
            # if missing locally or server is newer, update
            if not l or r['mod'] > l['mod']:
                self.col.decks.update(r)
        for r in rchg[1]:
            try:
                l = self.col.decks.getConf(r['id'])
            except KeyError:
                l = None
            # if missing locally or server is newer, update
            if not l or r['mod'] > l['mod']:
                self.col.decks.updateConf(r)

    # Tags
    ##########################################################################

    def getTags(self):
        if self.col.server:
            return [t for t, usn in self.col.tags.allItems()
                    if usn >= self.minUsn]
        else:
            tags = []
            for t, usn in self.col.tags.allItems():
                if usn == -1:
                    self.col.tags.tags[t] = self.maxUsn
                    tags.append(t)
            self.col.tags.save()
            return tags

    def mergeTags(self, tags):
        self.col.tags.register(tags, usn=self.maxUsn)

    # Cards/notes/revlog
    ##########################################################################

    def mergeRevlog(self, logs):
        self.col.db.executemany(
            "insert or ignore into revlog values (?,?,?,?,?,?,?,?,?)",
            logs)

    def newerRows(self, data, table, modIdx):
        ids = (r[0] for r in data)
        lmods = {}
        for id, mod in self.col.db.execute(
            "select id, mod from %s where id in %s and %s" % (
                table, ids2str(ids), self.usnLim())):
            lmods[id] = mod
        update = []
        for r in data:
            if r[0] not in lmods or lmods[r[0]] < r[modIdx]:
                update.append(r)
        self.col.log(table, data)
        return update

    def mergeCards(self, cards):
        self.col.db.executemany(
            "insert or replace into cards values "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            self.newerRows(cards, "cards", 4))

    def mergeNotes(self, notes):
        rows = self.newerRows(notes, "notes", 3)
        self.col.db.executemany(
            "insert or replace into notes values (?,?,?,?,?,?,?,?,?,?,?)",
            rows)
        self.col.updateFieldCache([f[0] for f in rows])

    # Col config
    ##########################################################################

    def getConf(self):
        return self.col.conf

    def mergeConf(self, conf):
        self.col.conf = conf

# Local syncing for unit tests
##########################################################################

class LocalServer(Syncer):

    # serialize/deserialize payload, so we don't end up sharing objects
    # between cols
    def applyChanges(self, changes):
        l = json.loads; d = json.dumps
        return l(d(Syncer.applyChanges(self, l(d(changes)))))

# HTTP syncing tools
##########################################################################

# Calling code should catch the following codes:
# - 501: client needs upgrade
# - 502: ankiweb down
# - 503/504: server too busy

class HttpSyncer(object):

    def __init__(self, hkey=None):
        self.hkey = hkey
        self.skey = checksum(str(random.random()))[:8]

    # Posting data as a file
    ######################################################################
    # We don't want to post the payload as a form var, as the percent-encoding is
    # costly. We could send it as a raw post, but more HTTP clients seem to
    # support file uploading, so this is the more compatible choice.

    def req(self, method, fobj=None, comp=6, hkey=True):
        postdata = {}
        postdata['c'] = int(bool(comp))
        if hkey:
            postdata['k'] = self.hkey
            postdata['s'] = self.skey
        if fobj:
            postdata['data'] = gzip.compress(fobj.read()) if comp else fobj
        r = requests.post(SYNC_URL+method, data=postdata, verify=CERT_PATH)
        r.raise_for_status();
        return r

# Incremental sync over HTTP
######################################################################

class RemoteServer(HttpSyncer):

    def __init__(self, hkey):
        HttpSyncer.__init__(self, hkey)

    def hostKey(self, user, pw):
        "RetURNS HKEY or none if user/pw incorrect."
        try:
            res = self.req("hostKey", json.dumps({'u': user, 'p': pw}), hkey=False)
            self.hkey = res.json()['key']
            return self.hkey
        except HTTPError as e:
            if e.response.status_code == requests.codes.forbidden:
                return none
            raise

    def meta(self):
        try:
            res = self.req("meta", StringIO(json.dumps(dict( v=SYNC_VER, cv="ankidesktop,%s,%s"%(anki.version, platDesc())))))
            return res.json()
        except HTTPError as e:
            if e.response.status_code == requests.codes.forbidden:
                return None
            raise

    def applyChanges(self, data):
        return self.req("applyChanges", json.dumps(data)).json()

    def start(self, data):
        return self.req("start", json.dumps(data)).json()

    def chunk(self, data):
        return self.req("chunk", json.dumps(data)).json()

    def applyChunk(self, data):
        return self.req("applyChunk", json.dumps(data)).json()

    def sanityCheck2(self, data):
        return self.req("sanityCheck2", json.dumps(data)).json()

    def finish(self, data):
        return self.req("finish", json.dumps(data)).json()

# Full syncing
##########################################################################

class FullSyncer(HttpSyncer):

    def __init__(self, col, hkey, con):
        HttpSyncer.__init__(self, hkey, con)
        self.col = col

    def download(self):
        runHook("sync", "download")
        self.col.close()
        res = self.req("download")
        tpath = self.col.path + ".tmp"
        if res.text == "upgradeRequired":
            runHook("sync", "upgradeRequired")
            return
        open(tpath, "wb").write(res.text)
        # check the received file is ok
        d = DB(tpath)
        assert d.scalar("pragma integrity_check") == "ok"
        d.close()
        # overwrite existing collection
        os.unlink(self.col.path)
        os.rename(tpath, self.col.path)
        self.col = None

    def upload(self):
        "True if upload successful."
        runHook("sync", "upload")
        # make sure it's ok before we try to upload
        if self.col.db.scalar("pragma integrity_check") != "ok":
            return False
        if not self.col.basicCheck():
            return False
        # apply some adjustments, then upload
        self.col.beforeUpload()
        if self.req("upload", open(self.col.path, "rb")).text != "OK":
            return False
        return True

# Media syncing
##########################################################################

class MediaSyncer(object):

    def __init__(self, col, server=None):
        self.col = col
        self.server = server
        self.added = None

    def sync(self, mediaUsn):
        # step 1: check if there have been any changes
        runHook("sync", "findMedia")
        lusn = self.col.media.usn()
        # if first sync or resync, clear list of files we think we've sent
        if not lusn:
            self.col.media.forceResync()
        self.col.media.findChanges()
        if lusn == mediaUsn and not self.col.media.hasChanged():
            return "noChanges"
        # step 1.5: if resyncing, we need to get the list of files the server
        # has and remove them from our local list of files to sync
        if not lusn:
            files = self.server.mediaList()
            need = self.col.media.removeExisting(files)
        else:
            need = None
        # step 2: send/recv deletions
        runHook("sync", "removeMedia")
        lrem = self.removed()
        rrem = self.server.remove(fnames=lrem, minUsn=lusn)
        self.remove(rrem)
        # step 3: stream files from server
        runHook("sync", "server")
        while 1:
            runHook("sync", "streamMedia")
            usn = self.col.media.usn()
            zipf = self.server.files(minUsn=usn, need=need)
            if self.addFiles(zipf):
                break
        # step 4: stream files to the server
        runHook("sync", "client")
        while 1:
            runHook("sync", "streamMedia")
            zipf, fnames = self.files()
            if not fnames:
                # finished
                break
            usn = self.server.addFiles(zipf)
            # after server has replied, safe to remove from log
            self.col.media.forgetAdded(fnames)
            self.col.media.setUsn(usn)
        # step 5: sanity check during beta testing
        # NOTE: when removing this, need to move server tidyup
        # back from sanity check to addFiles
        s = self.server.mediaSanity()
        c = self.mediaSanity()
        if c != s:
            # if the sanity check failed, force a resync
            self.col.media.forceResync()
            return "sanityCheckFailed"

    def removed(self):
        return self.col.media.removed()

    def remove(self, fnames, minUsn=None):
        self.col.media.syncRemove(fnames)
        if minUsn is not None:
            # we're the server
            return self.col.media.removed()

    def files(self):
        return self.col.media.zipAdded()

    def addFiles(self, zipf):
        "True if zipf is the last in set. Server returns new usn instead."
        return self.col.media.syncAdd(zipf)

    def mediaSanity(self):
        return self.col.media.sanityCheck()

# Remote media syncing
##########################################################################

class RemoteMediaServer(HttpSyncer):

    def __init__(self, hkey, con):
        HttpSyncer.__init__(self, hkey, con)

    def remove(self, **kw):
        return self.req("remove", json.dumps(kw)).json()

    def files(self, **kw):
        return self.req("files", json.dumps(kw))

    def addFiles(self, zipf):
        # no compression, as we compress the zip file instead
        return self.req("addFiles", zipf, comp=0).json()

    def mediaSanity(self):
        return self.req("mediaSanity").json()

    def mediaList(self):
        return self.req("mediaList").json()

    # only for unit tests
    def mediatest(self, n):
        return self.req("mediatest", json.dumps({'n': n})).json()
