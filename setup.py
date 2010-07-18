from distutils.core import setup
import py2exe

setup(console=['atd.py'], options = {"py2exe":{'packages':['gzip', 'lxml']}})
