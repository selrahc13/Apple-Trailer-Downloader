import base64
import datetime
from optparse import OptionParser
import os
import random
import re
import shelve
import shutil
import struct
import sys
import time
import unicodedata
import urllib2
from xml.etree.ElementTree import ElementTree

from BeautifulSoup import BeautifulSoup
import imdb


def mkdir(d):
    try:
        os.makedirs(d)
    except OSError:
        if os.path.isdir(d):
            # We are nearly safe
            pass
        else:
            # There was an error on creation, so make sure we know about it
            raise

def hash_file(path):
    try:
        longlongformat = 'q'  # long long
        bytesize = struct.calcsize(longlongformat)

        f = open(path, "rb")

        filesize = os.path.getsize(path)
        hash = filesize

        if filesize < 65536 * 2:
            return "SizeError"

        for x in range(65536/bytesize):
            buffer = f.read(bytesize)
            (l_value,)= struct.unpack(longlongformat, buffer)
            hash += l_value
            hash = hash & 0xFFFFFFFFFFFFFFFF #to remain as 64bit number


        f.seek(max(0,filesize-65536),0)
        for x in range(65536/bytesize):
            buffer = f.read(bytesize)
            (l_value,)= struct.unpack(longlongformat, buffer)
            hash += l_value
            hash = hash & 0xFFFFFFFFFFFFFFFF

        f.close()
        returnedhash =  "%016x" % hash
        return returnedhash

    except(IOError):
        return "IOError"

def _get_trailer_opener(url):
    #set user agent to current quicktime version
    user_agent = r"QuickTime/%s" % _get_QT_version('English', 'Windows')

    request = urllib2.Request(url)
    request.add_header('User-Agent', user_agent)
    opener = urllib2.urlopen(request)
    return opener

def _get_QT_version(lang, os):
    url = r"http://www.apple.com/quicktime/download/version.html"
    response = urllib2.urlopen(url)
    html = response.read()
    soup = BeautifulSoup(html)
    table = _walk_table(soup)
    for col in table[0]:
        if col.lower().count(os.lower()):
            column_index = table[0].index(col)
            break
    for row in table:
        if row[0].lower().count(lang.lower()):
            row_index = table.index(row)
            break
    ver = table[row_index][column_index]
    match = re.match(r"\d{1,2}\.\d{1,2}\.\d{1,2}", ver)
    if match:
        return ver
    return table[1][1]

def _walk_table(soup):
    ''' Parse out the rows of an HTML table.  Shamelessly stolen from the
        following because I'm lazy:
        http://www.jgc.org/blog/2009/11/parsing-html-in-python-with.html
    '''
    return [ [ col.renderContents() for col in row.findAll(['td', 'th']) ]
             for row in soup.find('table').findAll('tr') ]

def _get_current_trailer_info():
    current_trailers = r"http://www.apple.com/trailers/home/xml/current.xml"
    response = urllib2.urlopen(current_trailers)
    tree = ElementTree(file=response)
    idb = imdb.IMDb()

    #map of keys in our dictionary to XML paths in current.xml
    tags = {'title': 'info/title',
            'runtime': 'info/runtime',
            'rating': 'info/rating',
            'trailer_date': 'info/postdate',
            'release_date': 'info/releasedate',
            'description': 'info/description',
            'genre': 'genre/name',
            'poster': 'poster/location',
            'big_poster': 'poster/xlarge',
            'trailer': 'preview/large',
            'studio': 'info/studio',
            'director': 'info/director',
            'cast': 'cast/name' #this path will have multiple 'name' nodes
            }

    trailer_db = {}

    #information for each trailer is stored in it's own 'movieinfo' node
    #here we create list of Elements with each Element containing the tree for
    #one movie/trailer
    movies = tree.findall('movieinfo')

    trailer_db = shelve.open(db, writeback=True)

    if options.flush:
        #Delete all database entries
        trailer_db.clear()
    if options.redownload:
        #remove specified trailer/movie from db
        for trailer in trailer_db:
            if trailer_db[trailer] == options.redownload:
                _ = trailer_db.pop(movie)

    for movie in movies:
        #Process each 'movieinfo' Element
        id = movie.attrib['id']

        try:
            #if this succeeds, we already have info for this movie.  This means
            # we'll check to see if there is a new trailer for the movie
            _ = trailer_db[id].keys()
        except:
            trailer_db[id] = {}

        #Get all cast members.
        cast = movie.findall(tags['cast'])
        cast_l = []
        for name in cast:
            cast_l.append(sanitize(name.text))
        #build a string of cast members
        trailer_db[id]['cast'] = " / ".join(cast_l)

        for tag in tags:
            #For each tag we get the corresponding node and do special
            #processing for nodes that need it
            if tag == 'cast':
                continue

            txt = sanitize(movie.findtext(tags[tag]))

            if tag == 'trailer':
                #account for new trailers for already existing movies
                #structure: db[id]['trailer'][trailerurl][keys = trailer_keys]

                tr_url=txt #Save the url of the trailer for later use
                trailer_keys = ['dled', 'dl_date', 'dl_url', 'dl_hash', 'trailer_date']
                try:
                    #this succeeds if we don't yet have a 'trailer' key
                    if txt not in trailer_db[id][tag].keys():
                        trailer_db[id][tag][txt] = dict.fromkeys(trailer_keys)
                        trailer_db[id][tag][txt]['dled'] = False
                except:
                    #otherwise, we create the 'trailer' key
                    trailer_db[id][tag] = {txt:dict.fromkeys(trailer_keys)}
                    trailer_db[id][tag][txt]['dled'] = False
            elif tag == 'trailer_date':
                #temp store date this trailer came out
                tr_date = txt
            else:
                #store rest of tags in db
                trailer_db[id][tag] = txt

        #set trailer release date for specific trailer
        trailer_db[id]['trailer'][tr_url]['trailer_date'] = \
        datetime.datetime.strptime(tr_date, "%Y-%m-%d")

        try:
            trailer_db[id]['release_date'] = datetime.datetime.strptime(
                trailer_db[id]['release_date'], "%Y-%m-%d")
        except:
            pass

        #Now that we have the info to narrow down the search, let's parse
        #imdb for rating info on movies that Apple says are "not yet rated"
        if options.imdb:
            _rating = trailer_db[id]['rating']
            if _rating.lower() == "not yet rated":
                title = trailer_db[id]['title']
                print 'Attempting to fetch rating for %s from imdb.' % title
                title = title.lower()
                try:
                    year = trailer_db[id]['release_date'].year
                except:
                    #Make a guess at the release year
                    year = (datetime.datetime.today() + datetime.timedelta(weeks=24)).year
                mv = idb.search_movie("%s (%s)" % (title, year))[0]
                idb.update(mv)
                mpaa = mv.get('mpaa')
                if mpaa:
                    trailer_db[id]['rating'] = mpaa.split()[1]
                    print '\tMovie rated %s' % trailer_db[id]['rating']
                else:
                    print '\tMovie still has no rating'

        #Build the .nfo string.  I don't really store utf-8 because the
        #Windows console hates it, and I'm too lazy to do it properly
        m = trailer_db[id]
        xml = []
        xml.append(r'<?xml version="1.0" encoding="utf-8" standalone="yes"?>')
        xml.append("<movieinfo>")
        xml.append("\t<title>%s</title>" % m['title'])
        xml.append("\t<runtime>%s</runtime>" % m['runtime'])
        xml.append("\t<rating>%s</rating>" % m['rating'])
        xml.append("\t<studio>%s</studio>" % m['studio'])
        try:
            dt = datetime.datetime.strftime(m['release_date'], "%B %d, %Y")
        except:
            dt = ''
        xml.append("\t<releasedate>%s</releasedate>" % dt)

        xml.append("\t<director>%s</director>" % m['director'])
        xml.append("\t<description>%s</description>" % m['description'])
        xml.append("\t<genre>%s</genre>" % m['genre'])
        xml.append("\t<cast>%s</cast>" % m['cast'])
        xml.append("</movieinfo>")
        trailer_db[id]['xml'] = xml

    trailer_db.close()

def sanitize(text, fn=False):
    if not fn:
        if type(text) != type(unicode()):
            return text
    else:
        invalid_chars = r'<>:"/\|?*.'
        text = unicode(text)

    punctuation = { ord(u'\N{LEFT SINGLE QUOTATION MARK}'): ord(u"'"),
                   ord(u'\N{RIGHT SINGLE QUOTATION MARK}'): ord(u"'"),
                   ord(u'\N{LEFT DOUBLE QUOTATION MARK}'): ord(u'"'),
                   ord(u'\N{RIGHT DOUBLE QUOTATION MARK}'): ord(u'"'),
                   ord(u'\N{EM DASH}'): ord(u'-'),
                   ord(u'\N{EN DASH}'): ord(u'-')}
    valid_chars = []
    orig_text = unicode(text)
    text = text.translate(punctuation)
    text = unicodedata.normalize('NFKD', text)
    text = text.encode("cp1252", "replace")
    if fn:
        for char in text:
            if char not in invalid_chars:
                valid_chars.append(char)
    else:
        for char in text:
            valid_chars.append(char)
    if len(valid_chars) > 0:
        ret = ''.join(valid_chars)
        return ret
    else:
        #just return a gibberish, but safe, text
        return base64.urlsafe_b64encode(text)


def download_trailers():
    print "Fetching trailer list from Apple."
    _get_current_trailer_info()
    transferred = 0
    dl_db = shelve.open(db, writeback=True)
    curr_trailer_info = {}
    trailer_count = 0

    #process db and get list of trailers to download
    for movie in dl_db:
        trailers = dl_db[movie]['trailer']
        tit = dl_db[movie]['title']
        for trailer in trailers:
            urls = []
            download = False

            if not options.redownload \
            and not options.mdatelimit \
            and not options.tdatelimit:
                if not trailers[trailer]['dled']:
                    #if not downloaded
                    if options.fake:
                        print 'downloading because not downloaded: %s' % tit
                    urls.append(trailer)
                    continue
            if options.redownload == dl_db[movie]['title']:
                #if --redown option
                if options.fake:
                    print 'downloading because of --redown: %s' % tit
                urls.append(trailer)
                continue
            if options.mdatelimit:
                try:
                    #some movies don't have a release date yet
                    if dl_db[movie]['release_date'] > options.mdatelimit:
                        #if movie release is later than --mdatelimit
                        if options.fake:
                            print 'downloading because of release date: %s' % tit
                        #print 'release date: %s' % dl_db[movie]['release_date']
                        #print 'date limit: %s' % options.mdatelimit
                        #print '-'*78
                        urls.append(trailer)
                        continue
                except:
                    pass
            if options.tdatelimit:
                if dl_db[movie]['trailer'][trailer]['trailer_date'] > options.tdatelimit:
                    if options.fake:
                        print 'downloading because of trailer date: %s' % tit
                    urls.append(trailer)
                    continue


        trailer_count += len(urls)
        curr_trailer_info[movie] = urls
    print "%i trailers to fetch. (Limiting dl to %i MB)" % (trailer_count, options.downlimit)

    if trailer_count == 0:
        #no trailers to download
        return False

    #Start getting trailers
    dl_size = 0
    for movie in curr_trailer_info:
        if options.fake:
            continue
        title = dl_db[movie]['title']
        urls = curr_trailer_info[movie]

        for url in urls:
            #Check for highest res available
            for res in res_pref:
                try_res = re.sub("640w", res, url)

                #set up download
                try:
                    opener = _get_trailer_opener(try_res)
                except urllib2.HTTPError:
                    continue
                headers = opener.info().headers
                for header in headers:
                    #make sure file is a quicktime video
                    if header.lower().count('content-type:'):
                        if header.lower().count('video/quicktime'):
                            msg = 'Downloading trailer for: %s (%s)' % (title, res)
                            print msg
                            dl = True
                        else:
                            dl=False

                if dl:
                    #download and save file if it's a valid trailer
                    orig_ext = os.path.splitext(try_res)[1]
                    orig_fn = os.path.splitext(os.path.basename(try_res))[0]

                    #process filename options
                    if options.do_rename:
                        #rename file with movie's name
                        filename = sanitize(title, fn=True) + orig_ext
                    else:
                        filename = sanitize(orig_fn, fn=True) + orig_ext

                    if len(options.append_text) > 0:
                        #append text to file name
                        split = os.path.splitext(filename)
                        filename = "%s%s%s" % (split[0],
                                               options.append_text,
                                               split[1])
                    if options.extension:
                        #change extension
                        ext = options.extension
                        if not ext.startswith("."):
                            ext = "." + ext
                        split = os.path.splitext(filename)
                        filename = "%s%s" % (split[0], ext)

                    try:
                        f = open(filename, 'wb')
                    except:
                        raise NameError("Can't open %s for writing" % filename)

                    #download file
                    f.write(opener.read())
                    f.close()
                    size = os.path.getsize(filename)
                    hash1 = hash_file(filename)
                    mkdir(options.destination)

                    if not os.path.isfile(os.path.join(options.destination, filename)):
                        shutil.copy(filename, options.destination)
                    else:
                        #append an integer to end of filename if more than one
                        #trailer for movie
                        hash2 = hash_file(os.path.join(options.destination, filename))
                        append_count = 0
                        while 1:
                            if hash1 == hash2:
                                break

                            append_count += 1
                            append_txt = "-%s" % append_count
                            ext = os.path.splitext(filename)[1]
                            fn = os.path.splitext(filename)[0]
                            new_fn = fn+append_txt+ext

                            if not os.path.isfile(os.path.join(options.destination, new_fn)):
                                shutil.copy(filename, os.path.join(options.destination, new_fn))
                                filename = new_fn
                                break
                            else:
                                hash2 = hash_file(os.path.join(options.destination, fn+append_txt+ext))
                            if append_count > 10:
                                print "Potential Error.  We've tried 10 different filenames for %s" % title
                                print "Skipping..."
                                break

                    os.remove(filename)

                    if options.htenfo:
                        #write .nfo file for Home Theater Experience
                        nfo_path = os.path.join(options.destination,
                                                os.path.splitext(filename)[0] + ".nfo")
                        #create XML string
                        xml = dl_db[movie]['xml']
                        #create quality element
                        if res == '480' or res == '640w':
                            quality = 'standard'
                        else:
                            quality = res
                        xml.insert(-1, "\t<quality>%s</quality>" % quality)
                        for i in range(len(xml)):
                            xml[i] = xml[i].decode("utf_8", 'ignore')
                        try:
                            xml_string = "\n".join(xml)
                        except:
                            return xml
                            raise ValueError("Error building XML string")
                        f = open(nfo_path, "w")
                        f.write(xml_string)
                        f.close()

                    dl_db[movie]['trailer'][url]['dled'] = True
                    dl_db[movie]['trailer'][url]['dl_date'] = datetime.datetime.today()
                    dl_db[movie]['trailer'][url]['dl_url'] = try_res
                    dl_db[movie]['trailer'][url]['dl_hash'] = hash1
                    dl_size += size
                    #don't try any more resolutions
                    break
        if options.downlimit == 0:
            continue
        if dl_size > options.downlimit*1024*1024:
            break
    dl_db.close()
    return True

#CONFIG
db = 'trailer_dl.db'
res_pref = ['1080p', '720p', '480p', '640w', '480']
#standard, 480p, 720p, 1080p
#CONFIG

if __name__ == "__main__":
    usage = "usage: %prog [options]\n\nIf no options passed, it will download all not already downloaded trailers to a subdir called Trailers."
    parser = OptionParser(version="%prog .1", usage=usage)
    parser.add_option("-l", "--downlimit",
                      dest="downlimit",
                      metavar="MB",
                      help="Approxmiate megabytes to download per session (default: %default)",
                      type="int",
                      default=0)
    parser.add_option("-d", "--dest",
                      dest="destination",
                      metavar="DIR",
                      help="Destination directory. (default: %default)",
                      type="string",
                      default="Trailers")
    parser.add_option("-a","--append",
                      dest="append_text",
                      metavar="TEXT",
                      help="Appends the specified text to the filename. (default: %default)",
                      type="string",
                      default="-trailer")
    parser.add_option("-r", "--rename",
                      dest="do_rename",
                      help="Rename trailer with movies name.",
                      action="store_true")
    parser.add_option("-e", "--ext",
                      dest="extension",
                      help="Changes file extension to what is specified",
                      type="string")
    parser.add_option("--redown",
                      dest="redownload",
                      metavar="movie name",
                      help="Redownloads the trailer for the specified movie.  Ex: --redown Iron Man 2",
                      type="string")
    parser.add_option("--flush",
                      dest="flush",
                      help="WARNING: This option deletes your download history which means that all trailers will be downloaded again",
                      action="store_true")
    parser.add_option("--respref",
                      dest="respref",
                      help="Get specified resolution or less.  Options are %s" % res_pref)
    parser.add_option("--reslimit",
                      dest="reslimit",
                      help="Get specified resolution or dont get trailer at all")
    parser.add_option("--mdate",
                      dest="mdatelimit",
                      metavar="DATE",
                      help="Only get trailers for movies with a release date after this. (format: YYYY-MM-DD)")
    parser.add_option("--tdate",
                      dest="tdatelimit",
                      metavar="DATE",
                      help="Only get trailers released after this date. (format: YYYY-MM-DD)")
    parser.add_option("--fake",
                      dest="fake",
                      help="Don't download, just print list of movies it would download trailers for with the specified commandline. (Ignores download limit)",
                      action="store_true")
    parser.add_option("--htenfo",
                      dest="htenfo",
                      help="Writes an nfo file for use with the Home Theater Experience XBMC script.",
                      action="store_true")
    parser.add_option("--imdb",
                      dest="imdb",
                      help="Fetches missing information like MPAA rating from IMDB.  (Slows down parsing)",
                      action="store_true")

    (options, args) = parser.parse_args()

    #parse different options
    if options.respref:
        res_index = res_pref.index(options.respref)
        res_pref = res_pref[res_index:]
    if options.reslimit:
        res_index = res_pref.index(options.reslimit)
        res_pref = [res_pref[res_index]]
    if options.mdatelimit:
        try:
            options.mdatelimit = datetime.datetime.strptime(options.mdatelimit, "%Y-%m-%d")
        except:
            parser.error("Incorrectly formatted date.  Use '%Y-%m-%d'")
    if options.tdatelimit:
        try:
            options.tdatelimit = datetime.datetime.strptime(options.tdatelimit, "%Y-%m-%d")
        except:
            parser.error("Incorrectly formatted date.  Use '%Y-%m-%d'")

    if len(args) != 0:
        parser.error("Incorrectly formatted command line")

    xml = download_trailers()
