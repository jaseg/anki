# -*- coding: utf-8 -*-
# Copyright: petr.michalec@gmail.com
# License: GNU GPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import sys

from anki.stdmodels import addBasicModel
from anki.importing.noteimp import NoteImporter, ForeignNote, ForeignCard
from anki.lang import _
from anki.lang import ngettext

from xml.dom import minidom
from string import capwords
import re, unicodedata, time

class SmartDict(dict):
    """
    See http://www.peterbe.com/plog/SmartDict
    Copyright 2005, Peter Bengtsson, peter@fry-it.com

    A smart dict can be instanciated either from a pythonic dict
    or an instance object (eg. SQL recordsets) but it ensures that you can
    do all the convenient lookups such as x.first_name, x['first_name'] or
    x.get('first_name').
    """

    def __init__(self, *a, **kw):
        if a:
            if type(a[0]) is dict:
                kw.update(a[0])
            elif a[0].__class__.__name__=='SmartDict':
                kw.update(a[0].__dict__)

        dict.__init__(self, **kw)
        self.__dict__ = self

class SuperMemoElement(SmartDict):
  "SmartDict wrapper to store SM Element data"

  def __init__(self, *a, **kw):
    SmartDict.__init__(self, *a, **kw)
    #default content
    self.__dict__['lTitle'] = None
    self.__dict__['Title'] = None
    self.__dict__['Question'] = None
    self.__dict__['Answer'] = None
    self.__dict__['Count'] = None
    self.__dict__['Type'] = None
    self.__dict__['ID'] = None
    self.__dict__['Interval'] = None
    self.__dict__['Lapses'] = None
    self.__dict__['Repetitions'] = None
    self.__dict__['LastRepetiton'] = None
    self.__dict__['AFactor'] = None
    self.__dict__['UFactor'] = None



# This is an AnkiImporter
class SupermemoXmlImporter(NoteImporter):

    needMapper = False
    allowHTML = True

    """
    Supermemo XML export's to Anki parser.
    Goes through a SM collection and fetch all elements.

    My SM collection was a big mess where topics and items were mixed.
    I was unable to parse my content in a regular way like for loop on
    minidom.getElementsByTagName() etc. My collection had also an
    limitation, topics were splited into branches with max 100 items
    on each. Learning themes were in deep structure. I wanted to have
    full title on each element to be stored in tags.

    Code should be upgrade to support importing of SM2006 exports.
    """

    def __init__(self, *args):
        """Initialize internal varables.
        Pameters to be exposed to GUI are stored in self.META"""
        NoteImporter.__init__(self, *args)
        m = addBasicModel(self.col)
        m['name'] = "Supermemo"
        self.col.models.save(m)
        self.initMapping()

        self.lines = None
        self.numFields=int(2)

        # SmXmlParse VARIABLES
        self.xmldoc = None
        self.pieces = []
        self.cntBuf = [] #to store last parsed data
        self.cntElm = [] #to store SM Elements data
        self.cntCol = [] #to store SM Colections data

        # store some meta info related to parse algorithm
        # SmartDict works like dict / class wrapper
        self.cntMeta = SmartDict()
        self.cntMeta.popTitles = False
        self.cntMeta.title     = []

        # META stores controls of import scritp, should be
        # exposed to import dialog. These are default values.
        self.META = SmartDict()
        self.META.resetLearningData  = False            # implemented
        self.META.onlyMemorizedItems = False            # implemented
        self.META.loggerLevel = 2                       # implemented 0no,1info,2error,3debug
        self.META.tagAllTopics = True
        self.META.pathsToBeTagged = ['English for begginers', 'Advanced English 97', 'Phrasal Verbs']                # path patterns to be tagged - in gui entered like 'Advanced English 97|My Vocablary'
        self.META.tagMemorizedItems = True              # implemented
        self.META.logToStdOutput   = False              # implemented

        self.notes = []

## TOOLS

    def _fudgeText(self, text):
        "Replace sm syntax to Anki syntax"
        text = text.replace("\n\r", "<br>")
        text = text.replace("\n", "<br>")
        return text

    def _unicode2ascii(self,str):
        "Remove diacritic punctuation from strings (titles)"
        return "".join([ c for c in unicodedata.normalize('NFKD', str) if not unicodedata.combining(c)])

    def _decode_htmlescapes(self,s):
        """Unescape HTML code."""
        import bs4

        #my sm2004 also ecaped & char in escaped sequences.
        s = re.sub('&amp;','&',s)

        return str(bs4.BeautifulSoup(s))


    def _unescape(self,s,initilize):
        """Note: This method is not used, BeautifulSoup does better job.
        """

        if self._unescape_trtable == None:
            self._unescape_trtable = (
              ('&euro;','€'), ('&#32;',' '), ('&#33;','!'), ('&#34;','"'), ('&#35;','#'), ('&#36;','$'), ('&#37;','%'), ('&#38;','&'), ('&#39;',"'"),
              ('&#40;','('), ('&#41;',')'), ('&#42;','*'), ('&#43;','+'), ('&#44;',','), ('&#45;','-'), ('&#46;','.'), ('&#47;','/'), ('&#48;','0'),
              ('&#49;','1'), ('&#50;','2'), ('&#51;','3'), ('&#52;','4'), ('&#53;','5'), ('&#54;','6'), ('&#55;','7'), ('&#56;','8'), ('&#57;','9'),
              ('&#58;',':'), ('&#59;',';'), ('&#60;','<'), ('&#61;','='), ('&#62;','>'), ('&#63;','?'), ('&#64;','@'), ('&#65;','A'), ('&#66;','B'),
              ('&#67;','C'), ('&#68;','D'), ('&#69;','E'), ('&#70;','F'), ('&#71;','G'), ('&#72;','H'), ('&#73;','I'), ('&#74;','J'), ('&#75;','K'),
              ('&#76;','L'), ('&#77;','M'), ('&#78;','N'), ('&#79;','O'), ('&#80;','P'), ('&#81;','Q'), ('&#82;','R'), ('&#83;','S'), ('&#84;','T'),
              ('&#85;','U'), ('&#86;','V'), ('&#87;','W'), ('&#88;','X'), ('&#89;','Y'), ('&#90;','Z'), ('&#91;','['), ('&#92;','\\'), ('&#93;',']'),
              ('&#94;','^'), ('&#95;','_'), ('&#96;','`'), ('&#97;','a'), ('&#98;','b'), ('&#99;','c'), ('&#100;','d'), ('&#101;','e'), ('&#102;','f'),
              ('&#103;','g'), ('&#104;','h'), ('&#105;','i'), ('&#106;','j'), ('&#107;','k'), ('&#108;','l'), ('&#109;','m'), ('&#110;','n'),
              ('&#111;','o'), ('&#112;','p'), ('&#113;','q'), ('&#114;','r'), ('&#115;','s'), ('&#116;','t'), ('&#117;','u'), ('&#118;','v'),
              ('&#119;','w'), ('&#120;','x'), ('&#121;','y'), ('&#122;','z'), ('&#123;','{'), ('&#124;','|'), ('&#125;','}'), ('&#126;','~'),
              ('&#160;',' '), ('&#161;','¡'), ('&#162;','¢'), ('&#163;','£'), ('&#164;','¤'), ('&#165;','¥'), ('&#166;','¦'), ('&#167;','§'),
              ('&#168;','¨'), ('&#169;','©'), ('&#170;','ª'), ('&#171;','«'), ('&#172;','¬'), ('&#173;','­'), ('&#174;','®'), ('&#175;','¯'),
              ('&#176;','°'), ('&#177;','±'), ('&#178;','²'), ('&#179;','³'), ('&#180;','´'), ('&#181;','µ'), ('&#182;','¶'), ('&#183;','·'),
              ('&#184;','¸'), ('&#185;','¹'), ('&#186;','º'), ('&#187;','»'), ('&#188;','¼'), ('&#189;','½'), ('&#190;','¾'), ('&#191;','¿'),
              ('&#192;','À'), ('&#193;','Á'), ('&#194;','Â'), ('&#195;','Ã'), ('&#196;','Ä'), ('&Aring;','Å'), ('&#197;','Å'), ('&#198;','Æ'),
              ('&#199;','Ç'), ('&#200;','È'), ('&#201;','É'), ('&#202;','Ê'), ('&#203;','Ë'), ('&#204;','Ì'), ('&#205;','Í'), ('&#206;','Î'),
              ('&#207;','Ï'), ('&#208;','Ð'), ('&#209;','Ñ'), ('&#210;','Ò'), ('&#211;','Ó'), ('&#212;','Ô'), ('&#213;','Õ'), ('&#214;','Ö'),
              ('&#215;','×'), ('&#216;','Ø'), ('&#217;','Ù'), ('&#218;','Ú'), ('&#219;','Û'), ('&#220;','Ü'), ('&#221;','Ý'), ('&#222;','Þ'),
              ('&#223;','ß'), ('&#224;','à'), ('&#225;','á'), ('&#226;','â'), ('&#227;','ã'), ('&#228;','ä'), ('&#229;','å'), ('&#230;','æ'),
              ('&#231;','ç'), ('&#232;','è'), ('&#233;','é'), ('&#234;','ê'), ('&#235;','ë'), ('&#236;','ì'), ('&iacute;','í'), ('&#237;','í'),
              ('&#238;','î'), ('&#239;','ï'), ('&#240;','ð'), ('&#241;','ñ'), ('&#242;','ò'), ('&#243;','ó'), ('&#244;','ô'), ('&#245;','õ'),
              ('&#246;','ö'), ('&#247;','÷'), ('&#248;','ø'), ('&#249;','ù'), ('&#250;','ú'), ('&#251;','û'), ('&#252;','ü'), ('&#253;','ý'),
              ('&#254;','þ'), ('&#255;','ÿ'), ('&#256;','Ā'), ('&#257;','ā'), ('&#258;','Ă'), ('&#259;','ă'), ('&#260;','Ą'), ('&#261;','ą'),
              ('&#262;','Ć'), ('&#263;','ć'), ('&#264;','Ĉ'), ('&#265;','ĉ'), ('&#266;','Ċ'), ('&#267;','ċ'), ('&#268;','Č'), ('&#269;','č'),
              ('&#270;','Ď'), ('&#271;','ď'), ('&#272;','Đ'), ('&#273;','đ'), ('&#274;','Ē'), ('&#275;','ē'), ('&#276;','Ĕ'), ('&#277;','ĕ'),
              ('&#278;','Ė'), ('&#279;','ė'), ('&#280;','Ę'), ('&#281;','ę'), ('&#282;','Ě'), ('&#283;','ě'), ('&#284;','Ĝ'), ('&#285;','ĝ'),
              ('&#286;','Ğ'), ('&#287;','ğ'), ('&#288;','Ġ'), ('&#289;','ġ'), ('&#290;','Ģ'), ('&#291;','ģ'), ('&#292;','Ĥ'), ('&#293;','ĥ'),
              ('&#294;','Ħ'), ('&#295;','ħ'), ('&#296;','Ĩ'), ('&#297;','ĩ'), ('&#298;','Ī'), ('&#299;','ī'), ('&#300;','Ĭ'), ('&#301;','ĭ'),
              ('&#302;','Į'), ('&#303;','į'), ('&#304;','İ'), ('&#305;','ı'), ('&#306;','Ĳ'), ('&#307;','ĳ'), ('&#308;','Ĵ'), ('&#309;','ĵ'),
              ('&#310;','Ķ'), ('&#311;','ķ'), ('&#312;','ĸ'), ('&#313;','Ĺ'), ('&#314;','ĺ'), ('&#315;','Ļ'), ('&#316;','ļ'), ('&#317;','Ľ'),
              ('&#318;','ľ'), ('&#319;','Ŀ'), ('&#320;','ŀ'), ('&#321;','Ł'), ('&#322;','ł'), ('&#323;','Ń'), ('&#324;','ń'), ('&#325;','Ņ'),
              ('&#326;','ņ'), ('&#327;','Ň'), ('&#328;','ň'), ('&#329;','ŉ'), ('&#330;','Ŋ'), ('&#331;','ŋ'), ('&#332;','Ō'), ('&#333;','ō'),
              ('&#334;','Ŏ'), ('&#335;','ŏ'), ('&#336;','Ő'), ('&#337;','ő'), ('&#338;','Œ'), ('&#339;','œ'), ('&#340;','Ŕ'), ('&#341;','ŕ'),
              ('&#342;','Ŗ'), ('&#343;','ŗ'), ('&#344;','Ř'), ('&#345;','ř'), ('&#346;','Ś'), ('&#347;','ś'), ('&#348;','Ŝ'), ('&#349;','ŝ'),
              ('&#350;','Ş'), ('&#351;','ş'), ('&#352;','Š'), ('&#353;','š'), ('&#354;','Ţ'), ('&#355;','ţ'), ('&#356;','Ť'), ('&#357;','ť'),
              ('&#358;','Ŧ'), ('&#359;','ŧ'), ('&#360;','Ũ'), ('&#361;','ũ'), ('&#362;','Ū'), ('&#363;','ū'), ('&#364;','Ŭ'), ('&#365;','ŭ'),
              ('&#366;','Ů'), ('&#367;','ů'), ('&#368;','Ű'), ('&#369;','ű'), ('&#370;','Ų'), ('&#371;','ų'), ('&#372;','Ŵ'), ('&#373;','ŵ'),
              ('&#374;','Ŷ'), ('&#375;','ŷ'), ('&#376;','Ÿ'), ('&#377;','Ź'), ('&#378;','ź'), ('&#379;','Ż'), ('&#380;','ż'), ('&#381;','Ž'),
              ('&#382;','ž'), ('&#383;','ſ'), ('&#340;','Ŕ'), ('&#341;','ŕ'), ('&#342;','Ŗ'), ('&#343;','ŗ'), ('&#344;','Ř'), ('&#345;','ř'),
              ('&#346;','Ś'), ('&#347;','ś'), ('&#348;','Ŝ'), ('&#349;','ŝ'), ('&#350;','Ş'), ('&#351;','ş'), ('&#352;','Š'), ('&#353;','š'),
              ('&#354;','Ţ'), ('&#355;','ţ'), ('&#356;','Ť'), ('&#577;','ť'), ('&#358;','Ŧ'), ('&#359;','ŧ'), ('&#360;','Ũ'), ('&#361;','ũ'),
              ('&#362;','Ū'), ('&#363;','ū'), ('&#364;','Ŭ'), ('&#365;','ŭ'), ('&#366;','Ů'), ('&#367;','ů'), ('&#368;','Ű'), ('&#369;','ű'),
              ('&#370;','Ų'), ('&#371;','ų'), ('&#372;','Ŵ'), ('&#373;','ŵ'), ('&#374;','Ŷ'), ('&#375;','ŷ'), ('&#376;','Ÿ'), ('&#377;','Ź'),
              ('&#378;','ź'), ('&#379;','Ż'), ('&#380;','ż'), ('&#381;','Ž'), ('&#382;','ž'), ('&#383;','ſ'),
          )


      #m = re.match()
      #s = s.replace(code[0], code[1])

## DEFAULT IMPORTER METHODS

    def foreignNotes(self):

        # Load file and parse it by minidom
        self.loadSource(self.file)

        # Migrating content / time consuming part
        # addItemToCards is called for each sm element
        self.logger('Parsing started.')
        self.parse()
        self.logger('Parsing done.')

        # Return imported cards
        self.total = len(self.notes)
        self.log.append(ngettext("%d card imported.", "%d cards imported.", self.total) % self.total)
        return self.notes

    def fields(self):
        return 2

## PARSER METHODS

    def addItemToCards(self,item):
        "This method actually do conversion"

        # new anki card
        note = ForeignNote()

        # clean Q and A
        note.fields.append(self._fudgeText(self._decode_htmlescapes(item.Question)))
        note.fields.append(self._fudgeText(self._decode_htmlescapes(item.Answer)))
        note.tags = []

        # pre-process scheduling data
        # convert learning data
        if (not self.META.resetLearningData
            and item.Interval >= 1
            and getattr(item, "LastRepetition", None)):
            # migration of LearningData algorithm
            tLastrep = time.mktime(time.strptime(item.LastRepetition, '%d.%m.%Y'))
            tToday = time.time()
            card = ForeignCard()
            card.ivl = int(item.Interval)
            card.lapses = int(item.Lapses)
            card.reps = int(item.Repetitions) + int(item.Lapses)
            nextDue = tLastrep + (float(item.Interval) * 86400.0)
            remDays = int((nextDue - time.time())/86400)
            card.due = self.col.sched.today+remDays
            card.factor = int(float(item.AFactor.replace(',','.'))*1000)
            note.cards[0] = card

        # categories & tags
        # it's worth to have every theme (tree structure of sm collection) stored in tags, but sometimes not
        # you can deceide if you are going to tag all toppics or just that containing some pattern
        tTaggTitle = False
        for pattern in self.META.pathsToBeTagged:
            if item.lTitle != None and pattern.lower() in " ".join(item.lTitle).lower():
              tTaggTitle = True
              break
        if tTaggTitle or self.META.tagAllTopics:
          # normalize - remove diacritic punctuation from unicode chars to ascii
          item.lTitle = [ self._unicode2ascii(topic) for topic in item.lTitle]

          # Transfrom xyz / aaa / bbb / ccc on Title path to Tag  xyzAaaBbbCcc
          #  clean things like [999] or [111-2222] from title path, example: xyz / [1000-1200] zyx / xyz
          #  clean whitespaces
          #  set Capital letters for first char of the word
          tmp = list(set([ re.sub('(\[[0-9]+\])'   , ' ' , i ).replace('_',' ')  for i in item.lTitle ]))
          tmp = list(set([ re.sub('(\W)',' ', i )  for i in tmp ]))
          tmp = list(set([ re.sub( '^[0-9 ]+$','',i)  for i in tmp ]))
          tmp = list(set([ capwords(i).replace(' ','')  for i in tmp ]))
          tags = [ j[0].lower() + j[1:] for j in tmp if j.strip() != '']

          note.tags += tags

          if self.META.tagMemorizedItems and item.Interval >0:
            note.tags.append("Memorized")

          self.logger('Element tags\t- ' + repr(note.tags), level=3)

        self.notes.append(note)

    def logger(self,text,level=1):
        "Wrapper for Anki logger"

        dLevels={0:'',1:'Info',2:'Verbose',3:'Debug'}
        if level<=self.META.loggerLevel:
          #self.deck.updateProgress(_(text))

          if self.META.logToStdOutput:
            print(self.__class__.__name__+ " - " + dLevels[level].ljust(9) +' -\t'+ _(text))


    # OPEN AND LOAD
    def openAnything(self,source):
        "Open any source / actually only openig of files is used"

        if source == "-":
            return sys.stdin

        # try to open with urllib (if source is http, ftp, or file URL)
        import urllib.request, urllib.parse, urllib.error
        try:
            return urllib.request.urlopen(source)
        except (IOError, OSError):
            pass

        # try to open with native open function (if source is pathname)
        try:
            return open(source)
        except (IOError, OSError):
            pass

        # treat source as string
        import io
        return io.StringIO(str(source))

    def loadSource(self, source):
        """Load source file and parse with xml.dom.minidom"""
        self.source = source
        self.logger('Load started...')
        sock = open(self.source)
        self.xmldoc = minidom.parse(sock).documentElement
        sock.close()
        self.logger('Load done.')


    # PARSE
    def parse(self, node=None):
        "Parse method - parses document elements"

        if node==None and self.xmldoc!=None:
          node = self.xmldoc

        _method = "parse_%s" % node.__class__.__name__
        if hasattr(self,_method):
          parseMethod = getattr(self, _method)
          parseMethod(node)
        else:
          self.logger('No handler for method %s' % _method, level=3)

    def parse_Document(self, node):
        "Parse XML document"

        self.parse(node.documentElement)

    def parse_Element(self, node):
        "Parse XML element"

        _method = "do_%s" % node.tagName
        if hasattr(self,_method):
          handlerMethod = getattr(self, _method)
          handlerMethod(node)
        else:
          self.logger('No handler for method %s' % _method, level=3)
          #print traceback.print_exc()

    def parse_Text(self, node):
        "Parse text inside elements. Text is stored into local buffer."

        text = node.data
        self.cntBuf.append(text)

    #def parse_Comment(self, node):
    #    """
    #    Source can contain XML comments, but we ignore them
    #    """
    #    pass


    # DO
    def do_SuperMemoCollection(self, node):
        "Process SM Collection"

        for child in node.childNodes: self.parse(child)

    def do_SuperMemoElement(self, node):
        "Process SM Element (Type - Title,Topics)"

        self.logger('='*45, level=3)

        self.cntElm.append(SuperMemoElement())
        self.cntElm[-1]['lTitle'] = self.cntMeta['title']

        #parse all child elements
        for child in node.childNodes: self.parse(child)

        #strip all saved strings, just for sure
        for key in list(self.cntElm[-1].keys()):
          if hasattr(self.cntElm[-1][key], 'strip'):
            self.cntElm[-1][key]=self.cntElm[-1][key].strip()

        #pop current element
        smel = self.cntElm.pop()

        # Process cntElm if is valid Item (and not an Topic etc..)
        # if smel.Lapses != None and smel.Interval != None and smel.Question != None and smel.Answer != None:
        if smel.Title == None and smel.Question != None and smel.Answer != None:
          if smel.Answer.strip() !='' and smel.Question.strip() !='':

            # migrate only memorized otherway skip/continue
            if self.META.onlyMemorizedItems and not(int(smel.Interval) > 0):
              self.logger('Element skiped  \t- not memorized ...', level=3)
            else:
              #import sm element data to Anki
              self.addItemToCards(smel)
              self.logger("Import element \t- " + smel['Question'], level=3)

              #print element
              self.logger('-'*45, level=3)
              for key in list(smel.keys()):
                self.logger('\t%s %s' % ((key+':').ljust(15),smel[key]), level=3 )
          else:
            self.logger('Element skiped  \t- no valid Q and A ...', level=3)


        else:
          # now we know that item was topic
          # parseing of whole node is now finished

          # test if it's really topic
          if smel.Title != None:
            # remove topic from title list
            t = self.cntMeta['title'].pop()
            self.logger('End of topic \t- %s' % (t), level=2)

    def do_Content(self, node):
        "Process SM element Content"

        for child in node.childNodes:
          if hasattr(child,'tagName') and child.firstChild != None:
            self.cntElm[-1][child.tagName]=child.firstChild.data

    def do_LearningData(self, node):
        "Process SM element LearningData"

        for child in node.childNodes:
          if hasattr(child,'tagName') and child.firstChild != None:
            self.cntElm[-1][child.tagName]=child.firstChild.data

    # It's being processed in do_Content now
    #def do_Question(self, node):
    #    for child in node.childNodes: self.parse(child)
    #    self.cntElm[-1][node.tagName]=self.cntBuf.pop()

    # It's being processed in do_Content now
    #def do_Answer(self, node):
    #    for child in node.childNodes: self.parse(child)
    #    self.cntElm[-1][node.tagName]=self.cntBuf.pop()

    def do_Title(self, node):
        "Process SM element Title"

        t = self._decode_htmlescapes(node.firstChild.data)
        self.cntElm[-1][node.tagName] = t
        self.cntMeta['title'].append(t)
        self.cntElm[-1]['lTitle'] = self.cntMeta['title']
        self.logger('Start of topic \t- ' + " / ".join(self.cntMeta['title']), level=2)


    def do_Type(self, node):
        "Process SM element Type"

        if len(self.cntBuf) >=1 :
          self.cntElm[-1][node.tagName]=self.cntBuf.pop()


if __name__ == '__main__':

  # for testing you can start it standalone

  #file = u'/home/epcim/hg2g/dev/python/sm2anki/ADVENG2EXP.xxe.esc.zaloha_FINAL.xml'
  #file = u'/home/epcim/hg2g/dev/python/anki/libanki/tests/importing/supermemo/original_ENGLISHFORBEGGINERS_noOEM.xml'
  #file = u'/home/epcim/hg2g/dev/python/anki/libanki/tests/importing/supermemo/original_ENGLISHFORBEGGINERS_oem_1250.xml'
  file = str(sys.argv[1])
  impo = SupermemoXmlImporter(Deck(),file)
  impo.foreignCards()

  sys.exit(1)

# vim: ts=4 sts=2 ft=python
