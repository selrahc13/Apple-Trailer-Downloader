import datetime
from optparse import OptionParser
import os
import re
import shlex
import shutil
import struct
import time
import urllib2
from xml.etree.ElementTree import ElementTree

from pkg.BeautifulSoup import BeautifulSoup
import imdb
import pkg.y_serial_v052 as y_serial

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
            d = "%s%s%s" % (os.path.splitext(p)[0],
                                      "." + str(i),
                                      os.path.splitext(p)[1])

            if not os.path.isfile(d):
                shutil.copy(s, d)
                os.remove(s)
                return d

        raise NameError("Can't find valid filename for %s" % os.path.basename(s))


def _options():
    usage = "usage: %prog [options]\n\nIf no options passed, it will download all not already downloaded trailers to a subdir called Trailers."
    parser = OptionParser(version="%prog 2.0dev", usage=usage)
    parser.add_option("-d", "--dest",
                      dest="destination",
                      metavar="DIR",
                      help="Destination directory. (default: %default)",
                      type="string",
                      default="Trailers")

    (options, args) = parser.parse_args()

    return options

def sync_trailer(trailer1, trailer2):
    ''' Syncs trailer1 and trailer2 states to each other.  This entails:

        * Make sure both have same master url
        * Add potential resolutions together (no duplicates)
        * Sync the available resolution cache.  Keep newest cache refresh date.
        * Sync the urls dict
    '''

    if trailer1.url != trailer2.url:
        raise ValueError("Can only sync state info for the same trailer")

    potential_resolutions = trailer1.potential_res
    potential_resolutions.extend(trailer2.potential_res)
    potential_resolutions = list(set(potential_resolutions))

    available_resolutions = trailer1._rez_cache[1]
    available_resolutions.extend(trailer2._rez_cache[1])
    available_resolutions = list(set(available_resolutions))

    #select which cache date to use
    if trailer1._rez_cache[0] > trailer2._rez_cache[0]:
        available_resolutions = (trailer1._rez_cache[0], available_resolutions)
    else:
        available_resolutions = (trailer2._rez_cache[0], available_resolutions)

    if trailer1.urls == trailer2.urls:
        rezs = trailer1.urls
    else:
        rezs = trailer1.urls.keys()
        rezs.extend(trailer2.urls.keys())
        rezs = dict.fromkeys((set(rezs)))
        for res in rezs:
            if trailer1.urls.has_key(res) and trailer2.urls.has_key(res):
                #both trailers have this res
                if trailer1.urls[res].downloaded and trailer2.urls[res].downloaded:
                    #both trailers have a download datetime
                    if trailer1.urls[res].downloaded > trailer2.urls[res].downloaded:
                        #we use trailer1 if it's most recent
                        rezs[res] = trailer1.urls[res]
                    else:
                        #otherwise we use trailer2
                        rezs[res] = trailer2.urls[res]
                elif not trailer1.urls[res].downloaded and trailer2.urls[res].downloaded:
                    #trailer1 doesn't have a download time so we use trailer2
                    rezs[res] = trailer2.urls[res]
                elif trailer1.urls[res].downloaded and not trailer2.urls[res].downloaded:
                    #trailer2 doesn't have a download time so we use trailer1
                    rezs[res] = trailer1.urls[res]
                else:
                    #neither trailer has been downloaded so we just use trailer1
                    rezs[res] = trailer1.urls[res]
            elif trailer1.urls.has_key(res) and not trailer2.urls.has_key(res):
                #trailers2 doesnt have this res, so we use trailers1
                rezs[res] = trailer1.urls[res]
            elif not trailer1.urls.has_key(res) and trailer2.urls.has_key(res):
                #trailers1 doesn't have this res, so we use trailers2
                rezs[res] = trailer2.urls[res]

    trailer = Trailer(trailer1.date, trailer1.url, trailer1.movie_title)
    trailer.potential_res = potential_resolutions
    trailer._rez_cache = available_resolutions
    trailer.urls = rezs

    return trailer

def download_trailers(db, res):
    movies = [x[2] for x in db.selectdic("*", 'movies').values()]
    for movie in movies:
        print "Checking/downloading for %s" % movie.title
        movie.download_trailers(res)
        persist_movie(movie, db)

def persist_movie(movie, db):
    print "Saving %s to database" % movie.title
    tags = movie.get_tags()

    #check if movie is in our database
    persisted_movie = fetch_by_apple_id(movie.apple_id, db)
    if persisted_movie:
        #Movie is already stored, so we need to update our stored info
        movie = update_movie(persisted_movie, movie)
        delete_by_apple_id(movie.apple_id, db)
    try:
        db.insert(movie, tags, 'movies')
    except:
        import pdb; pdb.set_trace()

def compare_trailers(trailers1, trailers2):
    trailers = []
    already_synced = []

    for t1 in trailers1:
        for t2 in trailers2:
            if t1.url == t2.url:
                already_synced.append(t1.url)
                trailers.append(sync_trailer(t1, t2))

    for t2 in trailers2:
        for t1 in trailers1:
            if t2.url == t1.url and t1.url not in already_synced:
                trailers.append(sync_trailer(t2, t1))

    return trailers

def update_movie(movie1, movie2):
    ''' Syncs movie1 state with movie2 state.
        These include:
            # of trailers
            Movie release date
            Which trailers have been downloaded in which resolutions

        Typically movie1 will be our persisted movie.
    '''
    if movie1.apple_id != movie2.apple_id:
        raise ValueError("Cannot compare two different movies")

    movie1.trailers = compare_trailers(movie1.trailers, movie2.trailers)

    #update the movie release date
    movie1.release_date = movie2.release_date

    #update mpaa rating
    movie1.mpaa = movie2.mpaa

    return movie1

def update_movies(db):
    ''' This is the main function for freshening our database with current info
        from Apple.  It builds a list of all the current movies from Apple's
        XML listing of trailers (which is often somewhat incomplete,
        unfortunately) via the build_movies() call.  It then persists each movie
        to our database.

        The only parameter is "db" which is a reference to a y_serial database.
    '''
    movies = build_movies()
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

        self.release_date = None
        self.description = None
        self.apple_genre = None
        self.poster_url = None
        self.large_poster_url = None

        self.studio = None
        self.director = None
        self.cast = None
        self.trailers = []
        self.inst_on = datetime.datetime.today()
        self._parsexml(xml)
        self._getimdb()

    def download_trailers(self, res):
        for t in self.trailers:
            t.download(res)

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
        self.trailers.append(Trailer(trailer_date, trailer_url, self.title))

        #Find any other trailers for the movie.
        self.find_trailers(trailer_url)

    def find_trailers(self, url):
        urls = []
        for purl in self._build_other_trailer_urls(url):
            #just checking for file existance, don't need to download
            try:
                opener = _get_trailer_opener(purl)
            except urllib2.HTTPError:
                continue
            except:
                print "Unknown error with additional trailer finder"
                import pdb; pdb.set_trace()

            headers = opener.info().headers
            for header in headers:
                #make sure file is a quicktime video
                if header.lower().count('content-type:'):
                    if header.lower().count('video/quicktime'):
                        urls.append(purl)

        for u in urls:
            for t in self.trailers:
                #Make sure we don't already have this one
                if u != t.url:
                    #We can't know the date of these trailers so we just assign them today's date
                    self.trailers.append(Trailer(datetime.datetime.today(), u, self.title))


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
    def __init__(self, date, url, movie_title):
        self.movie_title = movie_title
        self.date = date
        self.url = url
        self.potential_res = ['1080p', '720p', '480p', '640w', '480', '320']
        self._rez_cache = (datetime.datetime.today(), [])
        self.urls = {}

    def download(self, res):
        res_choice = self.choose_res(res)
        if self.urls[res].downloaded:
            print "already downloaded"
            return
        if res_choice:
            self.urls[res].download()
        else:
            print "%s is not an available resolution" % res

    def build_urls(self, rezs):
        for res in rezs:
            self.urls[res] = TrailerUrl(res, self.url)

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
        if (datetime.datetime.today() - self._rez_cache[0]).days > 6 or len(self._rez_cache[1]) == 0:
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
                    import pdb; pdb.set_trace()

                headers = opener.info().headers
                for header in headers:
                    #make sure file is a quicktime video
                    if header.lower().count('content-type:'):
                        if header.lower().count('video/quicktime'):
                            rezs.append(res)

            #store resolutions in our cache along with the datetime
            self._rez_cache = (datetime.datetime.today(), rezs)

            #populate our list of urls for this trailer
            self.build_urls(rezs)

        return self._rez_cache[1]

    def __str__(self):
        return "<Trailer: %s>" % self.movie_title

    def __repr__(self):
        return self.__str__()

class TrailerUrl():
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

        opener = _get_trailer_opener(self.url)
        fn = self.filename(self.url)

        f = open(fn, 'wb')
        f.write(opener.read())
        f.close()

        self.downloaded = datetime.datetime.today()
        self.hash = hash_file(fn)
        self.size = os.path.getsize(fn)

        mkdir(options.destination)
        self.local_path = move_file(fn, os.path.join(options.destination, fn))

    def filename(self, url):
        orig = os.path.basename(url)
        ext = os.path.splitext(orig)[1]

        return orig

    def __str__(self):
        return "<Trailer url: %s>" % self.url

    def __repr__(self):
        return self.__str__()


options = _options()

if __name__ == "__main__":
    db = db_conx('atd.db')

    update_movies(db)
    download_trailers(db, '320')
