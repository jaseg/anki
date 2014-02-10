# Copyright: Damien Elmes <anki@ichi2.net>
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html
import getpass
import os
import sys
import argparse
import tempfile
import locale
import gettext as gettext_mod

from anki.lang import langDir

translation = gettext_mod.translation('anki', langDir(), fallback=True)
ngettext, gettext = translation.ngettext, translation.gettext

from aqt.qt import *
import anki.lang
from anki.consts import HELP_SITE
from anki.utils import isMac
from anki import version as _version

ANKI_VERSION=_version
ANKI_WEBSITE="http://ankisrs.net/"
ANKI_CHANGES="http://ankisrs.net/docs/changes.html"
ANKI_DONATE="http://ankisrs.net/support/"
ANKI_SHARED="https://ankiweb.net/shared/"
ANKI_UPDATE="https://ankiweb.net/update/desktop"
mw = None # set on init

moduleDir = os.path.split(os.path.dirname(os.path.abspath(__file__)))[0]

try:
    import aqt.forms
except ImportError as e:
    if "forms" in str(e):
        print("If you're running from git, did you run build_ui.sh?\n")
    raise

from anki.utils import checksum

# Dialog manager - manages modeless windows
##########################################################################

class DialogManager(object):

    def __init__(self):
        from aqt import addcards, browser, editcurrent
        self._dialogs = {
            "AddCards": [addcards.AddCards, None],
            "Browser": [browser.Browser, None],
            "EditCurrent": [editcurrent.EditCurrent, None],
        }

    def open(self, name, *args):
        (creator, instance) = self._dialogs[name]
        if instance:
            instance.setWindowState(Qt.WindowActive)
            instance.activateWindow()
            instance.raise_()
            return instance
        else:
            instance = creator(*args)
            self._dialogs[name][1] = instance
            return instance

    def close(self, name):
        self._dialogs[name] = [self._dialogs[name][0], None]

    def closeAll(self):
        "True if all closed successfully."
        for (n, (creator, instance)) in list(self._dialogs.items()):
            if instance:
                if not instance.canClose():
                    return False
                instance.forceClose = True
                instance.close()
                self.close(n)
        return True

dialogs = DialogManager()

# Language handling
##########################################################################
# Qt requires its translator to be installed before any GUI widgets are
# loaded, and we need the Qt language to match the gettext language or
# translated shortcuts will not work.

def setupLang(pm, app, force=None):
    try:
        locale.setlocale(locale.LC_ALL, '')
    except:
        pass
    lang = force or pm.meta["defaultLang"]
    dir = langDir()
    # gettext
    translation = gettext_mod.translation('anki', dir, languages=[lang], fallback=True)
    translation.install()
    # qt
    anki.lang.setLang(lang, local=False)
    if lang in ("he","ar","fa"):
        app.setLayoutDirection(Qt.RightToLeft)
    else:
        app.setLayoutDirection(Qt.LeftToRight)
    # qt
    _qtrans = QTranslator()
    if _qtrans.load("qt_" + lang, dir):
        app.installTranslator(_qtrans)

# App initialisation
##########################################################################

class AnkiApp(QApplication):

    # Single instance support on Win32/Linux
    ##################################################

    KEY = "anki"+checksum(getpass.getuser())
    SOCKET_TIMEOUT = 5000

    def __init__(self, argv):
        QApplication.__init__(self, argv)
        self._argv = argv

    def checkForRunningInstances(self, args):
        # we accept only one command line argument. if it's missing, send
        # a blank screen to just raise the existing window
        cmd = 'raise'
        if args.file:
            cmd = 'open '+os.path.abspath(args.file.name)
        if self.sendMsg(cmd):
            print("Already running; reusing existing instance.")
            return True
        else:
            # send failed, so we're the first instance or the
            # previous instance died
            QLocalServer.removeServer(self.KEY)
            self._srv = QLocalServer(self)
            self.connect(self._srv, SIGNAL("newConnection()"), self.onRecv)
            self._srv.listen(self.KEY)
            return False

    def sendMsg(self, txt):
        #FIXME are these transferring strings or bytes?
        sock = QLocalSocket(self)
        sock.connectToServer(self.KEY, QIODevice.WriteOnly)
        if not sock.waitForConnected(self.SOCKET_TIMEOUT):
            # first instance or previous instance dead
            return False
        sock.write(txt)
        if not sock.waitForBytesWritten(self.SOCKET_TIMEOUT):
            raise Exception("existing instance not emptying")
        sock.disconnectFromServer()
        return True

    def onRecv(self):
        sock = self._srv.nextPendingConnection()
        if not sock.waitForReadyRead(self.SOCKET_TIMEOUT):
            sys.stderr.write(sock.errorString())
            return
        buf = sock.readAll()
        self.emit(SIGNAL("appMsg"), buf)
        sock.disconnectFromServer()

    # OS X file/url handler
    ##################################################

    def event(self, evt):
        if evt.type() == QEvent.FileOpen:
            self.emit(SIGNAL("appMsg"), "open "+evt.file() or "raise")
            return True
        return QApplication.event(self, evt)

def parseArgs(argv):
    # py2app fails to strip this in some instances, then anki dies
    # as there's no such profile
    if isMac and len(argv) > 1 and argv[1].startswith("-psn"):
        argv = [argv[0]]
    parser = argparse.ArgumentParser()

    def readable_dir(prospective_dir):
        if not os.path.isdir(prospective_dir):
            raise Exception("readable_dir:{0} is not a valid path".format(prospective_dir))
        if os.access(prospective_dir, os.R_OK):
            return prospective_dir
        else:
            raise Exception("readable_dir:{0} is not a readable dir".format(prospective_dir))

    parser.add_argument("-b", "--base", type=readable_dir, help="path to base folder")
    parser.add_argument("-p", "--profile", help="profile name to load")
    parser.add_argument("-l", "--lang", help="interface language (en, de, etc)")
    parser.add_argument("-v", "--version", action="version", version="%(prog)s " + ANKI_VERSION)
    parser.add_argument("file", nargs="?", type=argparse.FileType('r'), help="File to open")
    return parser.parse_args(argv)

def run():
    global mw

    # parse args
    args = parseArgs(sys.argv)

    # on osx we'll need to add the qt plugins to the search path
    if isMac and getattr(sys, 'frozen', None):
        rd = os.path.abspath(moduleDir + "/../../..")
        QCoreApplication.setLibraryPaths([rd])

    if isMac:
        QFont.insertSubstitution(".Lucida Grande UI", "Lucida Grande")

    # create the app
    app = AnkiApp(sys.argv)
    QCoreApplication.setApplicationName("Anki")
    if app.checkForRunningInstances(args):
        # we've signaled the primary instance, so we should close
        return

    # disable icons on mac; this must be done before window created
    if isMac:
        app.setAttribute(Qt.AA_DontShowIconsInMenus)

    # we must have a usable temp dir
    try:
        tempfile.gettempdir()
    except:
        QMessageBox.critical(
            None, "Error", """\
No usable temporary folder found. Make sure C:\\temp exists or TEMP in your \
environment points to a valid, writable folder.""")
        return

    # qt version must be up to date
    if qtmajor <= 4 and qtminor <= 6:
        QMessageBox.warning(
            None, "Error", "Your Qt version is known to be buggy. Until you "
          "upgrade to a newer Qt, you may experience issues such as images "
          "failing to show up during review.")

    # profile manager
    from aqt.profiles import ProfileManager
    pm = ProfileManager(args.base, args.profile)

    # i18n
    setupLang(pm, app, args.lang)

    # remaining pm init
    pm.ensureProfile()

    # load the main window
    import aqt.main
    mw = aqt.main.AnkiQt(app, pm, args)
    app.exec_()

if __name__ == "__main__":
    run()
