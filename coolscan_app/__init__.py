"""Coolscan: a small Mac app for Nikon Coolscan film scanners, built on the nkscan driver.

The parts:
  imaging    writing PNG and TIFF files, reading a TIFF back
  colour     negative to positive, pictures for the screen, colour spaces and profiles
  dust       dust and scratch removal from the infrared picture
  enhance    grain reduction, faded-colour restoration, lining passes up
  notify     optional, kept out of git: telling the person a strip is done (banner, shortcut, ntfy)
  driver     what the driver notes about a pass, and its failures in plain words
  demo       a pretend scanner for trying the screens
  settings   the settings and how they are kept between runs
  rolls      file names, roll folders, film descriptions
  scanning   scanning frames and saving them
  app        the app itself: the scanner session, thumbnail, preview, focus, exposure
  server     the web page's server and the start-up
  web/       the page: index.html, style.css, app.js
"""
