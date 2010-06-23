import datetime
import os
import re
import struct
import urllib2
from xml.etree.ElementTree import ElementTree

from pkg.BeautifulSoup import BeautifulSoup
import imdb
import pkg.y_serial_v052 as y_serial

def build_trailers():
    movies = _fetchxml()

    for movie in movies:
        t = Trailer(movie)

def mkdir(d):
    ''' Tries to make a directory and avoid race conditions.
    '''
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
    ''' Generates a hopefully unique hash of a trailer.
    '''
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
    ''' Returns an urllib2 opener with the user agent set to the current version
        of QuickTime.
    '''
    user_agent = r"QuickTime/%s" % _get_QT_version('English', 'Windows')

    request = urllib2.Request(url)
    request.add_header('User-Agent', user_agent)
    opener = urllib2.urlopen(request)
    return opener

def _get_QT_version(lang, os):
    ''' We dynamically set our version of QuickTime by fetching the most recent
        version number from apple.com.

        Refer to http://www.apple.com/quicktime/download/version.html for values
        for the two parameters.

           lang: A language taken from the language column at the above url.
           os: A substring from a column header at the above url...e.g. "Windows"
    '''
    url = r"http://www.apple.com/quicktime/download/version.html"
    response = urllib2.urlopen(url)
    html = response.read()
    soup = BeautifulSoup(html)
    table = _walk_table(soup)

    #get our OS column index
    for col in table[0]:
        if col.lower().count(os.lower()):
            column_index = table[0].index(col)
            break

    #get our language row index
    for row in table:
        if row[0].lower().count(lang.lower()):
            row_index = table.index(row)
            break

    #Get the cell at column index, row index
    ver = table[row_index][column_index]
    match = re.match(r"\d{1,2}\.\d{1,2}\.\d{1,2}", ver)

    if match:
        return ver

    #If for some reason we don't have a valid version number just return the
    #upper left-most version number
    return table[1][1]

def _walk_table(soup):
    ''' Parse out the rows of an HTML table.  Shamelessly stolen from the
        following because I'm lazy:
        http://www.jgc.org/blog/2009/11/parsing-html-in-python-with.html

        This will be a list of lists.
    '''
    return [ [ col.renderContents() for col in row.findAll(['td', 'th']) ]
             for row in soup.find('table').findAll('tr') ]

def _fetchxml():
    ''' Get the xml file from apple describing all their current trailers.
        We then parse out the ElementTree elements for each Movie and return
        a them in a list.
    '''
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
        self._getimdb()

    def _parsexml(self, xml):
        ''' Get all the trailer attributes from the xml.
        '''
        self.title = xml.find('info/title').text
        self.runtime = xml.find('info/runtime').text
        self.mpaa = xml.find('info/rating').text
        self.date = datetime.datetime.strptime(xml.find('info/postdate').text, "%Y-%m-%d")

        #Some trailers don't have a release date yet
        try:
            self.release_date = datetime.datetime.strptime(xml.find('info/releasedate').text, "%Y-%m-%d")
        except:
            pass

        self.description = xml.find('info/description').text

        #Make a list of all the associated genre's
        self.apple_genre = [x.text for x in xml.findall('genre/name')]
        self.poster_url = xml.find('poster/location').text
        self.large_poster_url = xml.find('poster/xlarge').text
        self.trailer_url = [xml.find('preview/large').text]
        self.studio = xml.find('info/studio').text
        self.director = xml.find('info/director').text

        #Make a list of all the listed cast members
        self.cast = [x.text for x in xml.findall('cast/name')]

    def _getimdb(self):
        ''' A lot of movies don't have an MPAA rating when they're posted to Apple.
            Here we try to get their current rating from IMDb.
        '''
        if self.mpaa.lower() == 'not yet rated':
            i = imdb.IMDb()
            i_results = i.search_movie(self.title.lower())
            if self.release_date:
                year = self.release_date.year
            else:
                #guess at the year by adding 12 weeks to today
                year = (datetime.datetime.today() + datetime.timedelta(weeks=12)).year

            i_result = None

            #Use an exact title and year match to make sure we've found the
            #movie listing for this trailer.
            for result in i_results:
                if result['title'].lower() == self.title.lower() and result['year'] == year:
                    i_result = result
                    break

            if not i_result:
                #We didn't get a matching movie from imdb...most likely the result
                #of a bad guess at the release year, or improper title naming on
                #Apple or IMDb's site.
                self.mpaa = None
            else:
                #This is a list of MPAA ratings in descending order of restrictiveness
                cert_list = ["NC-17", "R", "PG-13", "PG", "G", "UNRATED"]

                #Have to update the movie object IMDbPy gave us so it contains rating info
                i.update(i_result)
                if i_result.has_key('certificates'):
                    usa_certs = []
                    for cert in i_result['certificates']:
                        #Parse out all the USA certs because USA certs seems to be what most
                        #software I'm familiar with care about
                        try:
                            rating = re.match(r"usa:(?P<rating>[a-zA-Z0-9- ]+)(\Z|:)", cert.lower()).group('rating').upper()
                            if rating in cert_list:
                                usa_certs.append(rating)
                        except:
                            pass

                    #Sort via cert_list and take least-restrictive rating
                    if len(usa_certs) > 0:
                        self.mpaa = sorted(usa_certs, key=cert_list.index)[-1]
                    else:
                        self.mpaa = None

                if not self.mpaa and i_result.has_key('mpaa'):
                    #Some movies have the mpaa field such as "Rated R for sexuality."
                    #We'll parse the rating out of it if available.
                    try:
                        self.mpaa = re.search(r"(?P<rating>[a-zA-Z0-9-]+) for", i_result['mpaa']).group('rating').upper()
                    except:
                        self.mpaa = None
                else:
                    self.mpaa = None

    def __str__(self):
        if self.release_date:
            return "<Title: %s, Trailer date: %s, Movie date: %s>" % (self.title,
                                                                  datetime.datetime.strftime(self.date, "%Y-%m-%d"),
                                                                  datetime.datetime.strftime(self.release_date, "%Y-%m-%d"))
        else:
            return "<Title: %s, Trailer date: %s, Movie date: %s>" % (self.title,
                                                                  datetime.datetime.strftime(self.date, "%Y-%m-%d"),
                                                                  self.release_date)


#movies = _fetchxml()
#not_rated_count = 0
#fetched_rating_count = 0
#for movie in movies:
    #t = Trailer(movie)

    #if t.mpaa.lower() == 'not yet rated':
        #not_rated_count += 1
        #pre = t.mpaa
        #post = t.mpaa
        #if pre != post and post != None:
            #print "MOVIE: %s" % t.title
            #print "pre_IMDB: %s" % pre
            #print "post_IMDB: %s" % post
            #fetched_rating_count += 1
            #print '-'*60