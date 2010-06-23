import datetime
import os
import re
import struct
import urllib2
from xml.etree.ElementTree import ElementTree

from pkg import BeautifulSoup
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

def _fetchxml():
    current_trailers = r"http://www.apple.com/trailers/home/xml/current.xml"
    response = urllib2.urlopen(current_trailers)
    tree = ElementTree(file=response)
    #information for each trailer is stored in it's own 'movieinfo' node
    #here we create list of Elements with each Element containing the tree for
    #one movie/trailer
    movies = tree.findall('movieinfo')
    return movies

class Trailer():
    def __init__(self, xml):
        ''' Takes a movieinfo node from Apple's trailer xml file.
        '''
        self.title = None
        self.runtime = None
        self.mpaa = None
        self.date = None
        self.release_date = None
        self.description = None
        self.apple_genre = None
        self.poster_url = None
        self.large_poster_url = None
        self.trailer_url = None
        self.studio = None
        self.director = None
        self.cast = None
        self._parsexml(xml)

    def _parsexml(self, xml):
        ''' Parses the xml
        '''
        self.title = xml.find('info/title').text
        self.runtime = xml.find('info/runtime').text
        self.mpaa = xml.find('info/rating').text
        self.date = datetime.datetime.strptime(xml.find('info/postdate').text, "%Y-%m-%d")
        try:
            self.release_date = datetime.datetime.strptime(xml.find('info/releasedate').text, "%Y-%m-%d")
        except:
            pass

        self.description = xml.find('info/description').text
        self.apple_genre = [x.text for x in xml.findall('genre/name')]
        self.poster_url = xml.find('poster/location').text
        self.large_poster_url = xml.find('poster/xlarge').text
        self.trailer_url = [xml.find('preview/large').text]
        self.studio = xml.find('info/studio').text
        self.director = xml.find('info/director').text
        self.cast = [x.text for x in xml.findall('cast/name')]

    def _getimdb(self):
        if self.mpaa.lower() == 'not yet rated':
            i = imdb.IMDb()
            i_results = i.search_movie(self.title.lower())
            if self.release_date:
                year = self.release_date.year
            else:
                #guess at the year by adding 12 weeks to today
                year = (datetime.datetime.today() + datetime.timedelta(weeks=12)).year

            i_result = None
            for result in i_results:
                if result['title'].lower() == self.title.lower() and result['year'] == year:
                    i_result = result
                    break

            if not i_result:
                self.mpaa = None
            else:
                i.update(i_result)
                if i_result.has_key('certificates'):
                    usa_certs = []
                    for cert in certs:
                        #Parse out all the USA certs because USA certs seems to be what most
                        #software I'm familiar with care about
                        usa_certs.append(re.match(r"usa:(?P<rating>[a-zA-Z0-9- ]+)(\Z|:)", cert.lower()).group('rating').upper())

                elif i_result.has_key('mpaa'):
                    try:
                        self.mpaa = re.search(r"(?P<rating>[a-zA-Z0-9-]+) for", i_result['mpaa']).group('rating').upper()
                    except:
                        self.mpaa = None
                else:
                    print "NO RATING INFO FROM IMDB"


    def __str__(self):
        if self.release_date:
            return "<Title: %s, Trailer date: %s, Movie date: %s>" % (self.title,
                                                                  datetime.datetime.strftime(self.date, "%Y-%m-%d"),
                                                                  datetime.datetime.strftime(self.release_date, "%Y-%m-%d"))
        else:
            return "<Title: %s, Trailer date: %s, Movie date: %s>" % (self.title,
                                                                  datetime.datetime.strftime(self.date, "%Y-%m-%d"),
                                                                  self.release_date)
movies = _fetchxml()
for movie in movies:
    t = Trailer(movie)
    print "MOVIE: %s" % t.title
    t._getimdb()
    print '-'*60