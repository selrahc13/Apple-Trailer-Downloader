import base64
import datetime
from optparse import OptionParser
import os
import rfc822
import re
import shlex
import shutil
import string
import struct
import sys
import time
import urllib2
from xml.etree.ElementTree import ElementTree

from pkg.BeautifulSoup import BeautifulSoup
import imdb
from pkg.optparse_fmt import IndentedHelpFormatterWithNL
import pkg.y_serial_v052 as y_serial

def date_filter(obj_list, dt, date_attrib, after = True, include_none=True):
    ''' Takes a list of objects and returns a list that contains each
        object with attribute specified in "date_attrib" after "dt" unless
        "after" is set to False, in which case it returns a list of
        objects before "dt".

        dt should be a datetime object
    '''

    objects = []

    for obj in obj_list:
        comp_date = obj.__dict__[date_attrib]
        if after:
            if comp_date:
                if comp_date > dt:
                    objects.append(obj)
            else:
                if include_none:
                    objects.append(obj)

        else:
            if comp_date:
                if comp_date:
                    objects.append(obj)
            else:
                if include_none:
                    objects.append(obj)

    return objects


def sanitized_filename(filename, file_location=None):
    ''' Used to sanitize text for use as a filename.  If file_location isn't
        provided, we don't create a test file.  Otherwise temporarily create a
        0-byte file with the sanitized filename to confirm it's validity.

        >>> sanitized_filename("Prince of Persia: the Sands of Time.mov", ".")
        'Prince of Persia the Sands of Time.mov'
    '''

    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    fn = ''.join(c for c in filename if c in valid_chars)

    if file_location:
        #test filename for validity on file system at file_location
        f = os.path.join(file_location, fn)
        try:
            open(f, 'w').close()
            os.remove(f)
            return fn
        except:
            fn = "atd-%s" % fn
            f = os.path.join(file_location, fn)
            try:
                open(f, 'w').close()
                os.remove(f)
                return fn
            except:
                raise NameError("Cannot build valid filename!")
    else:
        return fn

def move_file(s, d):
    ''' Argument d should include destination filename.
    '''
    if not os.path.isfile(d):
        shutil.copy(s, d)
        os.remove(s)
        return d
    else:
        source_hash = hash_file(s)

        if source_hash == hash_file(d):
            return d

        #try up to 10 filenames before failing
        for i in range(10):
            d = "%s%s%s" % (os.path.splitext(d)[0],
                                      "." + str(i),
                                      os.path.splitext(d)[1])

            if not os.path.isfile(d):
                shutil.copy(s, d)
                os.remove(s)
                return d

        raise NameError("Can't find valid filename for %s" % os.path.basename(s))


def _options():
    res_pref = ['1080p', '720p', '480p', '640w', '480', '320']

    usage = "usage: %prog [options]\n\nIf no options passed, it will download all not already downloaded trailers to a subdir called Trailers."
    parser = OptionParser(version="%prog 3.0dev", usage=usage, formatter=IndentedHelpFormatterWithNL())
    parser.add_option("-d", "--dest",
                      dest="destination",
                      metavar="DIR",
                      help="Destination directory. (default: %default)",
                      type="string",
                      default="Trailers")
    parser.add_option("-r", "--rename",
                      dest="rename_mask",
                      help='String representing how each trailer should be named on disk.  This string can include these variables:\n  %TITLE%:  The movies title.\n  %FN%:  The trailers original filename.\n  %EXT%:  The trailers original extension.\n  %DT%:  The date the trailer was posted to Apple.com\n  %DTD%:  The date the trailer was downloaded.\n  %RES%:  The resolution of the trailer.\n  %MPAA%:  The rating of the movie.\n------------------\nExamples:\n  atd.py -r "%TITLE% - %RES%.hdmov"\nWill result in trailers named:\n  Iron Man 2 - 720p.hdmov\nYou can also include path seperators to sort trailers into directories:\n  atd.py --rename="%MPAA%\%TITLE% - (%DT%).hdmov"\nResults in:\n  PG-13\Inception - (2010-05-12).hdmov',
                      type="string",
                      default="%FN%.%EXT%")
    parser.add_option("--mdate",
                      dest="mdatelimit",
                      metavar="DATE",
                      help="Only get trailers for movies with a release date after this. Includes movies with no release date.(format: YYYY-MM-DD)")
    parser.add_option("--tdate",
                      dest="tdatelimit",
                      metavar="DATE",
                      help="Only get trailers released after this date. (format: YYYY-MM-DD)")
    hmsg = "Get specified resolution or less.  Options are %s" % res_pref
    hmsg = hmsg + " (Default: %default)"
    parser.add_option("--respref",
                      dest="respref",
                      help=hmsg,
                      default='320')
    parser.add_option("-f", "--fake",
                      dest="fake",
                      help="Don't download, just create dummy files zero bytes long.",
                      action="store_true")

    (options, args) = parser.parse_args()

    if options.mdatelimit:
        try:
            options.mdatelimit = datetime.datetime.strptime(options.mdatelimit, '%Y-%m-%d')
        except:
            print "Invalid date format for --mdate.  Please use YYYY-MM-DD."
            sys.exit()

    if options.tdatelimit:
        try:
            options.tdatelimit = datetime.datetime.strptime(options.tdatelimit, '%Y-%m-%d')
        except:
            print "Invalid date format for --tdate.  Please use YYYY-MM-DD."
            sys.exit()

    if options.respref not in res_pref:
        print "Invalid respoution specified for --respref"
        sys.exit()

    return options

def sync_movie(old_movie, new_movie):
    '''
    '''
    synced_movie = old_movie
    if old_movie.apple_id != new_movie.apple_id:
        raise ValueError("Can only sync state info for the same movie")

    replace_attribs = ['title', 'runtime', 'mpaa', 'release_date', 'description',
                       'apple_genre', 'studio', 'director', 'cast']

    for attrib in replace_attribs:
        setattr(synced_movie, attrib, getattr(new_movie, attrib))
        if getattr(old_movie, attrib) != getattr(new_movie, attrib):
            print "Updated: %s ==> %s" % (getattr(old_movie, attrib), getattr(new_movie, attrib))

    for url in new_movie.poster_url:
        if url not in old_movie.poster_url:
            synced_movie.poster_url.append(url)
            print "Added new poster url"

    for url in new_movie.large_poster_url:
        if url not in old_movie.large_poster_url:
            synced_movie.large_poster_url.append(url)
            print "Added new large_poster url"

    for trailer in new_movie.trailers:
        if trailer not in old_movie.trailers:
            #We don't know about this particular trailer
            synced_movie.trailers[trailer] = new_movie.trailers[trailer]
            print "Found new trailer"
        else:
            #We do know about this trailer so we keep our old info
            synced_movie.trailers[trailer] = old_movie.trailers[trailer]
            #...However, we need to check if any of the resolutions for this
            #trailer have been downloaded since old_movie
            for res in new_movie.trailers[trailer].urls:
                if res in synced_movie.trailers[trailer].urls:
                    if synced_movie.trailers[trailer].urls[res].downloaded:
                        #We have this one marked as downloaded, so don't do anything else
                        continue
                    else:
                        #We don't have this downloaded, so just copy our new state
                        synced_movie.trailers[trailer].urls[res] = new_movie.trailers[trailer].urls[res]
                else:
                    #We don't have this res, so just copy our new state
                    synced_movie.trailers[trailer].urls[res] = new_movie.trailers[trailer].urls[res]
                    #...This also means new_movie found new resolutions so copy it's _res_fetched attribute
                    synced_movie.trailers[trailer]._rez_fetched = new_movie.trailers[trailer]._rez_fetched

    for trailer in synced_movie.trailers:
        synced_movie.trailers[trailer].movie_title == synced_movie.title

    return synced_movie

def download_trailers(db, res):
    movies = [x[2] for x in db.selectdic("*", 'movies').values()]

    if options.mdatelimit:
        movies = date_filter(movies, options.mdatelimit, 'release_date')
    if options.tdatelimit:
        trailer_date_filtered = []
        for movie in movies:
            for trailer in movie.trailers:
                if movie.trailers[trailer].date > options.tdatelimit:
                    trailer_date_filtered.append(movie)
                    break
        movies = trailer_date_filtered

    for movie in movies:
        if isinstance(movie, Movie):
            #try:
                #print movie.trailers[movie.trailers.keys()[0]].urls[res].downloaded
            #except:
                #pass
            print '*'*50
            print "Checking/downloading for %s" % movie.title
            movie.download_trailers(res)
            persist_movie(movie, db)

def persist_movie(movie, db):
    tags = movie.get_tags()

    #check if movie is in our database
    persisted_movie = fetch_by_apple_id(movie.apple_id, db)

    if persisted_movie:
        #Movie is already stored, so we need to update our stored info
        print "Updating %s in database" % movie.title

        movie = sync_movie(persisted_movie, movie)

        delete_by_apple_id(movie.apple_id, db)
    else:
        print "Saving %s to database" % movie.title

    try:
        db.insert(movie, tags, 'movies')
    except:
        raise ValueError("DB ERROR: %s, %s" % (movie.title, tags))

def update_movies(db):
    ''' This is the main function for freshening our database with current info
        from Apple.  It builds a list of all the current movies from Apple's
        XML listing of trailers (which is often somewhat incomplete,
        unfortunately) via the build_movies() call.  It then persists each movie
        to our database.

        The only parameter is "db" which is a reference to a y_serial database.
    '''
    movies = build_movies(db)
    if not movies:
        return
    for movie in movies:
        persist_movie(movie, db)


def fetch_by_apple_id(apple_id, db):
    ''' Fetches the movie object for the specified apple_id from the database
    '''
    try:
        return db.select('apple_id:%s' % apple_id, 'movies')
    except:
        return

def delete_by_apple_id(apple_id, db):
    db.delete('apple_id:%s' % apple_id, 'movies')
    if not db.select('apple_id:%s' % apple_id, 'movies'):
        return True
    return False

def build_movies(db=None):
    movies_xml = _fetchxml(db)
    if not movies_xml:
        return
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
    return '7.0.0'

def _walk_table(soup):
    ''' Parse out the rows of an HTML table.  Shamelessly stolen from the
        following because I'm lazy:
        http://www.jgc.org/blog/2009/11/parsing-html-in-python-with.html

        This will be a list of lists.
    '''
    return [ [ col.renderContents() for col in row.findAll(['td', 'th']) ]
             for row in soup.find('table').findAll('tr') ]

def _fetchxml(db=None):
    ''' Get the xml file from apple describing all their current trailers.
        We then parse out the ElementTree elements for each Movie and return
        a them in a list.

        If we receive a reference to our db, we check to see if the date in
        current.xml has changed...if not we return None.
    '''
    current_trailers = r"http://www.apple.com/trailers/home/xml/current.xml"
    response = urllib2.urlopen(current_trailers)
    tree = ElementTree(file=response)
    if db:
        #date checking
        date = tree.getroot().attrib['date']
        d = rfc822.parsedate(date)
        date = datetime.datetime(d[0], d[1], d[2], d[3], d[4])
        try:
            stored_date = db.select('current_xml_date', 'movies')
        except:
            stored_date = datetime.datetime(year=2000, month = 1, day = 1)
        if date <= stored_date:
            print "Apple trailers list hasn't been updated"
            return
        else:
            try:
                db.delete('current_xml_date', 'movies')
            except:
                pass
            db.insert(date, 'current_xml_date', 'movies')
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

        self.release_date = None
        self.description = None
        self.apple_genre = None
        self.poster_url = None
        self.large_poster_url = None

        self.studio = None
        self.director = None
        self.cast = None
        self.trailers = {}
        self.inst_on = datetime.datetime.today()
        self.updated_on = datetime.datetime.today()
        self._parsexml(xml)
        self._getimdb()

    def download_trailers(self, res):
        for t in self.trailers:
            download = self.trailers[t].download(res)
            if not download:
                return
            fn = os.path.splitext(os.path.basename(self.trailers[t].urls[res].local_path))[0]
            ext = os.path.splitext(os.path.basename(self.trailers[t].urls[res].local_path))[1][1:]
            if self.mpaa:
                rating = self.mpaa
            else:
                rating = 'NR'
            tags = {'%TITLE%': self.title,
                    '%FN%': fn,
                    '%EXT%': ext,
                    '%DT%': datetime.datetime.strftime(self.trailers[t].date, '%Y-%m%d'),
                    '%DTD%': datetime.datetime.strftime(self.trailers[t].urls[res].downloaded, '%Y-%m%d'),
                    '%RES%': res,
                    '%MPAA%': rating
                    }
            new_fn = options.rename_mask

            for tag in tags:
                while 1:
                    _ = new_fn
                    new_fn = re.sub(tag, tags[tag], new_fn)

                    if _ == new_fn:
                        #nothing left to change for this tag
                        break

            self.move_trailer(t, new_fn, res)

            print "Saved to %s" % self.trailers[t].urls[res].local_path

    def move_trailer(self, trailer_key, dest_fn, res):
        mkdir(options.destination)
        dest = sanitized_filename(os.path.splitext(dest_fn)[0], file_location=options.destination) + os.path.splitext(dest_fn)[1]
        dest = os.path.join(os.path.join(options.destination, dest))
        source = self.trailers[trailer_key].urls[res].local_path

        self.trailers[trailer_key].urls[res].local_path = move_file(source, dest)

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

        #Some trailers don't have a release date yet
        try:
            self.release_date = datetime.datetime.strptime(xml.find('info/releasedate').text, "%Y-%m-%d")
        except:
            pass

        self.description = xml.find('info/description').text

        #Make a list of all the associated genre's
        self.apple_genre = [x.text for x in xml.findall('genre/name')]
        self.poster_url = [xml.find('poster/location').text]
        self.large_poster_url = [xml.find('poster/xlarge').text]
        #self.trailer_url = [xml.find('preview/large').text]
        self.studio = xml.find('info/studio').text
        self.director = xml.find('info/director').text

        #Make a list of all the listed cast members
        self.cast = [x.text for x in xml.findall('cast/name')]

        #Build a Trailer() for this trailer
        trailer_url = xml.find('preview/large').text
        trailer_date = datetime.datetime.strptime(xml.find('info/postdate').text, "%Y-%m-%d")
        self.trailers[trailer_url] = Trailer(trailer_date, trailer_url, self.title)

        #Find any other trailers for the movie.
        self.find_trailers(trailer_url)

    def find_trailers(self, url):
        urls = []
        other_urls = self._build_other_trailer_urls(url)

        for purl in other_urls:
            #just checking for file existance, don't need to download
            try:
                opener = _get_trailer_opener(purl)
            except urllib2.HTTPError:
                continue
            except:
                print "Unknown error with additional trailer finder"

            headers = opener.info().headers
            for header in headers:
                #make sure file is a quicktime video
                if header.lower().count('content-type:'):
                    if header.lower().count('video/quicktime'):
                        urls.append(purl)

        for u in urls:
            if u not in self.trailers:
                self.trailers[u] = Trailer(datetime.datetime.today(), u, self.title)

    def _build_other_trailer_urls(self, url):
        potential_urls = []
        try:
            trailer_number = int(re.search(r"tlr(?P<num>\d)", url).group('num'))
        except:
            return []
        if trailer_number == 1:
            return []
        for i in range(1, trailer_number-1):
            potential_urls.append(re.sub(r"tlr\d", "tlr%s" % i, url))

        return potential_urls

    def have_trailer(self, trailer_url):
        ''' Checks our list of trailers to see if we already know about
            trailer_url.
        '''
        for trailer in self.trailers:
            if trailer_url == self.trailers[trailer].url:
                return trailer
        return False

    def _getimdb(self):
        ''' A lot of movies don't have an MPAA rating when they're posted to Apple.
            Here we try to get their current rating from IMDb.
        '''
        if self.mpaa.lower() == 'not yet rated':
            i = imdb.IMDb()
            #try to access imdb up to 3 times
            for x in range(3):
                try:
                    i_results = i.search_movie(self.title.lower())
                    fail = False
                    break
                except:
                    fail = True
                    time.sleep(1)

            if fail:
                print "Failed to connect to imdb"
                self.mpaa = None
                return

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

    def __repr__(self):
        return "<Movie: %s>" % self.title

class Trailer():
    def __init__(self, date, url, movie_title, potential_res=None):
        self.movie_title = movie_title
        self.date = date
        self.url = url
        if not potential_res:
            self.potential_res = ['1080p', '720p', '480p', '640w', '480', '320']
        self._rez_fetched = datetime.datetime.today()
        self.urls = {}

    def download(self, res):
        res_choice = self.choose_res(res)
        if not res_choice:
            print "Can't choose res for %s" % self.movie_title
            return None
        if res_choice:
            self.urls[res].download()
            return True
        else:
            print "%s is not an available resolution" % res

    def build_urls(self, rezs):
        for res in rezs:
            self.urls[res] = TrailerResUrl(res, self.url)

    def choose_res(self, target_res, go_higher=False, exact=True):
        if exact:
            if target_res not in self.available_res:
                return None

        if target_res not in self.potential_res:
            raise ValueError("Invalid resolution.")

        if target_res in self.available_res:
            #easy choice...what we want is available
            return target_res

        else:
            tres_index = self.potential_res.index(target_res)
            highest_index = len(self.potential_res)-1
            while 1:
                if go_higher:
                    tres_index = tres_index + 1
                else:
                    tres_index = tres_index - 1

                if tres_index > highest_index or tres_index < 0:
                    #out of bounds
                    return

                if self.potential_res[tres_index] in self.available_res:
                    return self.potential_res[tres_index]

    def res_url(self, res):
        try:
            url = re.sub(re.search(r"_h(?P<res>.*)\.mov", self.url).group('res'), res, self.url)
        except:
            url = ''
        return url

    #treat method as attribute to save on calls to apple.com
    @property
    def available_res(self):
        #go fetch available resolutions only if it's been more than 6 days
        if (datetime.datetime.today() - self._rez_fetched).days > 6 or len(self.urls) == 0:
            rezs = []
            for res in self.potential_res:
                #build the url for the resolution
                url = self.res_url(res)
                if not url:
                    continue

                #just checking for file existance, don't need to download
                try:
                    opener = _get_trailer_opener(url)
                except urllib2.HTTPError:
                    continue
                except:
                    print "Unknown error with trailer resolution finder (http)"

                headers = opener.info().headers
                for header in headers:
                    #make sure file is a quicktime video
                    if header.lower().count('content-type:'):
                        if header.lower().count('video/quicktime'):
                            rezs.append(res)

            #store datetime for cache purposes
            self._rez_fetched = datetime.datetime.today()

            #populate our list of urls for this trailer
            self.build_urls(rezs)

            return rezs
        else:
            return self.urls.keys()

    def __str__(self):
        return "<Trailer: %s>" % self.movie_title

    def __repr__(self):
        return self.__str__()

class TrailerResUrl():
    def __init__(self, res, master_url):
        self.master_url = master_url
        self.res = res
        self.url = self.build_url()
        self.downloaded = False
        self.size = 0
        self.local_path = None
        self.hash = None

    def build_url(self):
        try:
            url = re.sub(re.search(r"_h(?P<res>.*)\.mov", self.master_url).group('res'), self.res, self.master_url)
        except:
            url = ''
        return url

    def download(self):
        if self.downloaded:
            print "already downloaded"
            return
        self.local_path = os.path.abspath(self.filename(self.url))
        if not fake:
            opener = _get_trailer_opener(self.url)

            f = open(self.local_path, 'wb')
            f.write(opener.read())
            f.close()
        else:
            open(self.local_path, 'w').close()
        self.downloaded = datetime.datetime.today()
        self.hash = hash_file(self.local_path)
        self.size = os.path.getsize(self.local_path)

    def filename(self, url):
        orig = os.path.basename(url)
        ext = os.path.splitext(orig)[1][1:]
        fn = os.path.splitext(orig)[0]

        return orig

    def __str__(self):
        return "<Trailer url: %s>" % self.url

    def __repr__(self):
        return self.__str__()


options = _options()

if __name__ == "__main__":
    if options.fake:
        fake = True
    else:
        fake = False
    db = db_conx('atd.db')

    update_movies(db)
    download_trailers(db, options.respref)