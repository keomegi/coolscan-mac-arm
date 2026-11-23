"""Coolscan: a small Mac app for Nikon Coolscan film scanners, built on the nkscan driver.

The parts:
  app          the app itself: the scanner session, thumbnail, preview, focus, exposure
  server       the web page's server and the start-up
  settings     the settings and how they are kept between runs
  rolls        file names, roll folders, film descriptions
  keepawake    keeping the Mac awake while a job runs
  scanner/     talking to the scanner
    driver       what the driver notes about a pass, and its failures in plain words
    demo         a pretend scanner for trying the screens (and for the tests)
    scanning     scanning frames and saving them
  imaging/     the pictures
    files        writing PNG and TIFF files, reading a TIFF back
    colour       negative to positive, pictures for the screen, colour spaces and profiles
    dust         dust and scratch removal from the infrared picture
    enhance      grain reduction, faded-colour restoration, lining passes up
    autocrop     finding the picture inside a frame
    develop      re-developing finished pictures from RAW scans
  extras/      optional, kept out of git: notify (messages when a strip is done), testmode (--test)
  web/         the page: index.html, style.css, app.js, develop.html, develop.js
"""
