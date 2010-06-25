import datetime
import os
import re
import shlex
import struct
import time
import urllib2
from xml.etree.ElementTree import ElementTree

from pkg.BeautifulSoup import BeautifulSoup
import imdb
import pkg.y_serial_v052 as y_serial

def persist_movie(movie, db):
    print "Saving %s to database" % movie.title
    tags = movie.get_tags()
    persisted_movie = fetch_by_apple_id(movie.apple_id, db)
    if persisted_movie:
        print "\t%s in database already, updating" % movie.title
        movie = update_movie(persisted_movie, movie)
        delete_by_apple_id(movie.apple_id, db)
    try:
        db.insert(movie, tags, 'movies')
    except:
        import pdb; pdb.set_trace()

def update_movie(movie1, movie2):
    ''' If both movies are the same, see if movie2 contains information for a
        new trailer.  If so, add new trailer info to movie1 and return it,
        otherwise we return movie1 unmodified.

        Additionally, we update the movie release date.
    '''
    if movie1.apple_id != movie2.apple_id:
        raise ValueError("Cannot compare two different movies")

    new_trailers = []

    #check each trailer in movie2
    for trailer2 in movie2.trailers:
        #assume it's new
        is_new_trailer = True

        #comparing to each trailer in movie1...
        for trailer1 in movie1.trailers:
            if trailer2.url == trailer1.url:
                #Assumed wrong, it's not new because trailer in movie1 has same url
                is_new_trailer = False

        #If we assumed correctly...
        if is_new_trailer:
            #...we add the trailer to our list of new trailers...
            new_trailers.append(trailer2)

    #...and add it to movie1
    movie1.trailers.extend(new_trailers)

    #update the movie release date
    movie1.release_date = movie2.release_date

    return movie1

def fetch_by_apple_id(apple_id, db):
    ''' Fetches the movie object for the specified apple_id from the database
    '''
    try:
        return db.select('apple_id:%s' % apple_id, 'movies')
    except:
        print "fail to fetch"


def delete_by_apple_id(apple_id, db):
    db.delete('apple_id:%s' % apple_id, 'movies')
    if not db.select('apple_id:%s' % apple_id, 'movies'):
        return True
    return False

def build_movies():
    movies_xml = _fetchxml()
    movies = []

    count = 0
    for movie_xml in movies_xml:
        print "Fetching movie info: %s/%s" % (count, len(movies_xml)) + "\r",
        movies.append(Movie(movie_xml))
        count += 1
    print
    return movies

def db_conx(filename):
    if not os.path.exists(filename):
        open(filename, 'w').close()

    db_path = os.path.abspath(filename)
    print "Database location: %s" % db_path
    return y_serial.Main(db_path)

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

class Movie():
    def __init__(self, xml):
        ''' Takes a movieinfo node from Apple's trailer xml file.
        '''
        self.apple_id = None
        self.title = None
        self.runtime = None
        self.mpaa = None
        #self.date = None
        self.release_date = None
        self.description = None
        self.apple_genre = None
        self.poster_url = None
        self.large_poster_url = None
        #self.trailer_url = None
        self.studio = None
        self.director = None
        self.cast = None
        self.trailers = []
        self.inst_on = datetime.datetime.today()
        self._parsexml(xml)
        self._getimdb()

    def _make_tag(self, text):
        return "#'%s'" % text

    def get_tags(self, string=True):
        ''' This generates a space seperated string of "tags" for a movie.  This
            contains (if available):
                movie title
                release date
                genres
                director
                cast members
                mpaa rating
        '''
        tags = []

        tags.append(self.title)
        if self.release_date:
            tags.append(datetime.datetime.strftime(self.release_date, "%Y-%m-%d"))
        for c in self.cast:
            tags.append(c)
        tags.append("mpaa:%s" % self.mpaa)
        tags.append("apple_id:%s" % self.apple_id)

        tags2 = []
        for tag in tags:
            tags2.append(self._make_tag(tag))

        if string:
            return ' '.join(tags2)
        else:
            return tags2

    def _parsexml(self, xml):
        ''' Get all the trailer attributes from the xml.
        '''
        self.apple_id = xml.attrib['id']
        self.title = xml.find('info/title').text
        self.runtime = xml.find('info/runtime').text
        self.mpaa = xml.find('info/rating').text
        #self.date = datetime.datetime.strptime(xml.find('info/postdate').text, "%Y-%m-%d")

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
        #self.trailer_url = [xml.find('preview/large').text]
        self.studio = xml.find('info/studio').text
        self.director = xml.find('info/director').text

        #Make a list of all the listed cast members
        self.cast = [x.text for x in xml.findall('cast/name')]

        #Build a Trailer() for this trailer
        trailer_url = xml.find('preview/large').text
        trailer_date = datetime.datetime.strptime(xml.find('info/postdate').text, "%Y-%m-%d")
        self.trailers.append(Trailer(trailer_date, trailer_url))

    def _getimdb(self):
        ''' A lot of movies don't have an MPAA rating when they're posted to Apple.
            Here we try to get their current rating from IMDb.
        '''
        if self.mpaa.lower() == 'not yet rated':
            i = imdb.IMDb()
            try:
                i_results = i.search_movie(self.title.lower())
            except:
                raise ValueError("Error accesing IMDb")
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
            return "<Title: %s, Trailers: %s, Movie date: %s, MPAA: %s>" % (self.title,
                                                                  len(self.trailers),
                                                                  datetime.datetime.strftime(self.release_date, "%Y-%m-%d"),
                                                                  self.mpaa)
        else:
            return "<Title: %s, Trailers: %s, Movie date: %s, MPAA: %s>" % (self.title,
                                                                  len(self.trailers),
                                                                  self.release_date,
                                                                  self.mpaa)

class Trailer():
    def __init__(self, date, url):
        self.date = date
        self.url = url
        self.downloaded = False
        self.potential_res = ['1080p', '720p', '480p', '640w', '480', '320']
        self._rez_cache = (datetime.datetime.today(), [])

    def download(self):
        pass

    #treat method as attribute to save on calls to apple.com
    @property
    def available_res(self):
        #go fetch available resolutions only if it's been more than 6 days
        if (datetime.datetime.today() - self._rez_cache[0]).days > 6 or len(self._rez_cache[1]) == 0:
            rezs = []
            for res in self.potential_res:
                #build the url for the resolution
                try:
                    url = re.sub(re.search(r"_h(?P<res>.*)\.mov", self.url).group('res'), res, self.url)
                except:
                    continue

                #just checking for file existance, don't need to download
                try:
                    opener = _get_trailer_opener(url)
                except urllib2.HTTPError:
                    continue
                except:
                    print "Unknown error with trailer resolution finder (http)"
                    import pdb; pdb.set_trace()

                headers = opener.info().headers
                for header in headers:
                    #make sure file is a quicktime video
                    if header.lower().count('content-type:'):
                        if header.lower().count('video/quicktime'):
                            rezs.append(res)

            #store resolutions in our cache along with the datetime
            self._rez_cache = (datetime.datetime.today(), rezs)
            print "fetched"
        else:
            print "FETCHED"

        return self._rez_cache[1]


db = db_conx('atd.db')
