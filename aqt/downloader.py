# Copyright: Damien Elmes <anki@ichi2.net>
# -*- coding: utf-8 -*-
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import time, re, traceback
from aqt.qt import *
from aqt import ngettext, gettext as _
from aqt.utils import showWarning
from anki.hooks import addHook, remHook
import aqt.sync # monkey-patches httplib2 FIXME
import requests

def download(mw, code):
    "Download addon/deck from AnkiWeb. On success caller must stop progress diag."
    # check code is valid
    try:
        code = int(code)
    except ValueError:
        showWarning(_("Invalid code."))
        return
    # create downloading thread
    thread = Downloader(code)
    def onRecv():
        try:
            mw.progress.update(label="%dKB downloaded" % (thread.recvTotal/1024))
        except NameError:
            # some users report the following error on long downloads
            # NameError: free variable 'mw' referenced before assignment in enclosing scope
            # unsure why this is happening, but guard against throwing the
            # error
            pass
    mw.connect(thread, SIGNAL("recv"), onRecv)
    thread.start()
    mw.progress.start(immediate=True)
    while not thread.isFinished():
        mw.app.processEvents()
        thread.wait(100)
    if not thread.error:
        # success
        return thread.data, thread.fname
    else:
        mw.progress.finish()
        showWarning(_("Download failed: %s") % thread.error)

class Downloader(QThread):

    def __init__(self, code):
        QThread.__init__(self)
        self.code = code
        self.error = None

    def run(self):
        # setup progress handler
        self.byteUpdate = time.time()
        self.recvTotal = 0
        def canPost():
            if (time.time() - self.byteUpdate) > 0.1:
                self.byteUpdate = time.time()
                return True
        def recvEvent(bytes):
            self.recvTotal += bytes
            if canPost():
                self.emit(SIGNAL("recv"))
        addHook("httpRecv", recvEvent)
        try:
            res = requests.get(aqt.appShared + "download/%d" % self.code)
        except: #FIXME
            self.error = traceback.format_exc()
            return
        finally:
            remHook("httpRecv", recvEvent)
        if resp['status'] == '200':
            self.error = None
            #FIXME
            self.fname = re.match("attachment; filename=(.+)",
                                  resp['content-disposition']).group(1)
            self.data = cont
        elif resp['status'] == '403':
            self.error = _("Invalid code.")
        else:
            self.error = _("Error downloading: %s") % resp['status']
