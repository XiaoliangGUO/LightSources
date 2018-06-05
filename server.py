import http.server
import socketserver
from urllib.parse import urlparse, parse_qs, unquote_plus

import sqlite3
import json
import sys
import os

class RequestHandler(http.server.SimpleHTTPRequestHandler):

    static_folder = "static"
    assets_dir = "/assets"

    # GESTION DES ROUTES

    def init_url(self):
        url = urlparse(unquote_plus(self.path))
        self.route = url.path.split('/')
        self.query = parse_qs(url.query)

        length = self.headers.get('Content-Length')
        ctype = self.headers.get('Content-Type')

        if length:
            self.body = str(self.rfile.read(int(length)),'utf-8')

            if ctype == 'application/json': 
                self.query.update(json.loads(self.body))
        else:
            self.body = ''

    def catch_url(self, pattern, path):
        pattern = pattern.split('/')
        catched = True

        k = 0
        while catched and k < len(pattern):
            if k >= len(path):
                catched = False
            elif (pattern[k] == '*') or (pattern[k] != '*' and pattern[k] == path[k]):
                k += 1
            else:
                catched = False

        return catched

    def do_HEAD(self):
        self.send_local()

    def do_GET(self):
        self.init_url()

        # SERVICES
        if self.catch_url("/service", self.route):
            self.send_json({
                "file" : "strange"
            })
    
        # FICHIERS STATIQUES
        elif self.catch_url(self.assets_dir, self.route):
            self.send_asset()
        elif self.catch_url("/home", self.route):
            self.send_html_file("/home.html")

        # REDIRECTION VERS LA PAGE D'ACCUEIL
        elif self.catch_url("/", self.route):
            self.send_response(301)
            self.send_header('Location', "/home")
            self.end_headers()
        
        # PAGE INTROUVABLE
        else:
            self.send_error(404, "Page introuvable")
    
    def do_POST(self):
        self.init_url()
        self.send_error(405)
    
    # METHODES D'ENVOI DE DONNEES

    def send_asset(self):
        method = "do_{}".format(self.command)
        getattr(http.server.SimpleHTTPRequestHandler, method)(self)
    
    def send_utf8(self, encoded, headers=[]):
        self.send_response(200)
        self.send_header("Content-Length", int(len(encoded)))
        self.send_header('Access-Control-Allow-Origin','*')
        [self.send_header(*t) for t in headers]
        self.end_headers()
        self.wfile.write(encoded)
    
    def send_html(self, html, headers=[]):
        body = bytes(html, 'utf-8')
        headers.append(("Content-Type", "text/html"))
        self.send_utf8(body, headers)
    
    def send_html_file(self, filename, headers=[]):
        with open(self.static_folder + filename, "r") as f:
            html = f.read()
        self.send_html(html, headers)
    
    def send_json(self, data, headers=[]):
        body = bytes(json.dumps(data), "utf-8")
        headers.append(("Content-Type", "application/json"))
        self.send_utf8(body, headers)

    # SURCOUCHE DE LA BASE DE DONNEES

    def fetch_assoc(self, cursor, sql, params=[]):
        cursor.execute(sql, params)
        data = cursor.fetchall()
        fields = cursor.description

        result = [{fields[k][0] : row[k] for k in range(len(fields))} for row in data] if data else []
        return result if (len(result) - 1) else (result[0] if len(result) else None)

port = 8080
print("Running on port {}".format(port))

conn = sqlite3.connect("scenes.sqlite")

httpd = socketserver.TCPServer(("", port), RequestHandler)
httpd.serve_forever()
