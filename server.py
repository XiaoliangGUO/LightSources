import http.server
import socketserver
from urllib.parse import urlparse, parse_qs, unquote_plus

import sqlite3
import json
import sys
import os

class RequestHandler(http.server.SimpleHTTPRequestHandler):

    static_folder = "static"
    scenes_folder = "rendering"
    assets_folder = "assets"

    static_dir = "/" + static_folder
    scenes_dir = "/" + scenes_folder
    assets_dir = "/" + assets_folder
    

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
            # renvoie la liste des scenes
            if self.catch_url("/service/scenes", self.route):
                cursor = conn.cursor()
                data = self.fetch_assoc(cursor, "SELECT id, name FROM scene")
                cursor.close()
                self.send_json(data) if data else self.send_error(404, "Database is empty")
            
            # renvoie les informations relatives à une scene
            # identifiée par son nom ou son identifiant
            elif self.catch_url("/service/scene/*", self.route):
                id = self.route[3]
                cursor = conn.cursor()
                data = self.fetch_assoc(cursor, "SELECT * FROM scene WHERE id = ? OR name = ?", (id, id))
                cursor.close()
                self.send_json(data) if data else self.send_error(404, "No scene could be found")
            
            # renvoie la liste des images
            elif self.catch_url("/service/images", self.route):
                cursor = conn.cursor()
                data = self.fetch_assoc(cursor, "SELECT id, filename FROM scene")
                cursor.close()
                self.send_json(data) if data else self.send_error(404, "Database is empty")
            
            # renvoie la liste des images
            elif self.catch_url("/service/image/*", self.route):
                id = self.route[3]
                cursor = conn.cursor()
                data = self.fetch_assoc(cursor, "SELECT filename FROM scene WHERE id = ? OR name = ?", (id, id))
                cursor.close()
                if data:
                    self.send_response(301)
                    self.send_header('Location', "../../../" + data['filename'])
                    self.end_headers()
                else:
                    self.send_error(404, "No scene could be found")

            # renvoie une erreur : adresse introuvable
            # (la méthode n'existe pas)
            elif self.catch_url("/service/*", self.route):
                self.send_error(404)
            
            # renvoie une erreur : syntaxe incompréhensible 
            # par le serveur (l'adresse est exactement /service)
            else:
                self.send_error(400)
    
        # RENDUS
        elif self.catch_url(self.scenes_dir, self.route):
            self.send_asset()

        # FICHIERS STATIQUES
        elif self.catch_url(self.assets_dir, self.route):
            self.send_asset()

        elif self.catch_url("/home", self.route):
            self.send_html_file("/home.html")
        
        elif self.catch_url("/api", self.route):
            self.send_html_file("/api.html")

        # REDIRECTION VERS LA PAGE D'ACCUEIL
        elif self.catch_url("/", self.route):
            self.send_response(301)
            self.send_header('Location', "/home")
            self.end_headers()
        
        # PAGE INTROUVABLE
        else:
            self.send_error(404, "File cannot be found")
    
    def do_POST(self):
        self.init_url()

        if self.catch_url("/service", self.route):

            if self.catch_url("/service/add", self.route):
                if not('name' in self.body) or not('serial' in self.body):
                    self.send_error(400, "Missing parameters")

                info = {"name":name, "serial":serial}
                for k in ("width", "height", "ptime", "filename"):
                    if k in self.body:
                        scene[k] = self.body[k]
                    
                # on soumet la requête INSERT
                fields = info.keys()
                values = list(info.values())
                query = "INSERT INTO scene ({}) VALUES({})".format(','.join(fields),','.join(['?']*len(fields)))
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql, values)

                # problème de duplication du nom
                except sqlite3.IntegrityError as e:
                    self.send_error(400, str(e))
                    cursor.close()
                    return
                
                if "width" in self.body and self.body["width"] > 0 and "height" in self.body and self.body["image"] > 0:
                    filename = self.body["filename"] if "filename" in self.body else ''
                    scene = self.create_image(info['name'],info['width'], info['height'], info['serial'], filename)
                    filename = scene.filename

                    cursor.execute("UPDATE scene SET ptime = ?, filename = ? WHERE name = ?",
                                    (scene.ptime, filename, info['name']))
                    if not cursor.rowcount:
                        self.error(500,'Could not update scene record')
                        conn.commit()
                        cursor.close()
                        return
                
                # on renvoie la nouvelle scène avec tous ses champs
                data = self.fetch_assoc(cursor,"SELECT * FROM scene WHERE name = ?", (info['name']))
                conn.commit()
                cursor.close()
                self.send_json(data) if data else self.send_error(500, 'Could not refetch scene record')
                
            elif self.catch_url("/service/update", self.route):
                if not('id' in self.body):
                    self.send_error(400, "Missing parameters")

                id = self.body['id']
                filename = ""

                cursor = conn.cursor()
                data = self.fetch_assoc(cursor,
                "SELECT name, width, height, serial, filename FROM scene WHERE id = ? OR name = ?", (id, id))
                if not data:
                    self.send_error(404)
                    cursor.close()
                    return

                (name, width, height, serial, filename) = [data[k] for k in data]

                # il faut recalculer l'image
                if (('width' in self.body and self.body['width'] and not self.body['width'] == width) or \
                    ('height' in self.body and self.body['height'] and not self.body['height'] == height) or \
                    ('serial' in self.body and self.body['serial'] and not self.body['serial'] == serial) or \
                    ('filename' in self.body and self.body['filename'] and not filename)) and not \
                    ('filename' in self.body and not self.body['filename']):
                try:
                    if not 'filename' in self.body:
                        self.body['filename'] = filename
                        scene = self.create_image( name,
                        self.body['width'] if 'width' in self.body else width,
                        self.body['height'] if 'height' in self.body else height,
                        self.body['serial'] if 'serial' in self.body else serial,
                        self.body['filename'])
                except ValueError as e:
                    self.send_error(400, str(e))
                    cursor.close()
                    return

                self.body['filename'] = scene.filename
                self.body['ptime'] = scene.ptime

                # il faut supprimer l'image
                if ('filename' in self.body and not self.body['filename']) or \
                ('width' in self.body and not self.body['width']) or \
                ('height' in self.body and not self.body['height']) or \
                ('serial' in self.body and not self.body['serial']):
                    if os.path.isfile(filename):
                        os.remove(filename)
                        self.body['ptime'] = 0

                # il faut déplacer l'image
                if 'filename' in self.body and self.body['filename'] and not self.body['filename'] == filename:
                    if os.path.isfile(filename):
                        os.rename(filename, self.body['filename'])

                # préparation des modifications demandées, pour construction de la requête SQL
                sqlset = []
                params = []
                for k in ('serial', 'width', 'height', 'ptime', 'filename'):
                    if k in self.body:
                        sqlset.append("{} = ?".format(k))
                        params.append(self.body[k])

                # il y a des infos à mettre à jour
                if (len(params)):
                params.append(id)
                params.append(id)
                sql = "UPDATE scene SET {} WHERE id = ? OR name = ?".format(', '.join(sqlset)) 
                cursor.execute(sql, params)

                # problème
                if not cursor.rowcount:
                    self.send_error(500, 'Could not update scene')
                    cursor.close()
                    return

                # Récupération de la scène à jour
                data = self.fetch_assoc(cursor,
                    "SELECT * FROM scene WHERE id = ? OR NAME = ?",(id, id))
                self.send_json(data) if data else self.send_error(500, 'Could not refetch scene')

                conn.commit()
                cursor.close()
                return

            elif self.catch_url("/service/remove", self.route):
                if not('id' in self.body):
                    self.send_error(400, "Missing parameters")

                cursor = conn.cursor()
                id = self.body['id']
                filename = ""

                data = self.fetch_assoc(cursor, "SELECT filename FROM scene WHERE id = ? OR name = ?", (id, id))
                if not data:
                    self.send_error(404, "Scene cannot be found")
                    cursor.close()
                    return
                filename = data['filename']

                # suppression de l'enregistrement
                cursor.execute("DELETE FROM scene WHERE id = ? OR NAME = ?",(id, id))
                if not cursor.rowcount:
                    self.send_error(500,'Could not delete scene')
                    cursor.close()
                    return

                # suppression du fichier
                if filename:
                    if os.path.isfile(filename):
                        os.remove(filename)
                        self.send_json({"status": "deleted"})
                    else:
                        self.send_error(500,'Image already vanished')
                    else:
                self.send_json({"status": "ok"})

                conn.commit()
                cursor.close()
                return

            else:
                self.send_error(400, "Service not found")

        else:
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

    def add_url(self, data):
        if 'filename' in data:
            split_filename = data['filename'].split("/")
            if(split_filename[0] == self.scenes_folder):
                data['url'] = "/".join(split_filename[1:])
        return data

    # SURCOUCHE DE LA BASE DE DONNEES

    def fetch_assoc(self, cursor, sql, params=[]):
        cursor.execute(sql, params)
        data = cursor.fetchall()
        fields = cursor.description

        result = [{fields[k][0] : row[k] for k in range(len(fields))} for row in data] if data else []
        return result if (len(result) - 1) else (result[0] if len(result) else None)

    # CRÉATION DES SCENES

    def create_image(self, name, width, height, serial, filename):
        if not filename:
            filename = self.scenes_folder + '/{}.png'.format(name)
        scene = Scene.deserialize(serial)
        scene.initialize(width, height)
        scene.trace(True)
        scene.save_image(filename)
        return scene

port = 8080
print("Running on port {}".format(port))

conn = sqlite3.connect("scenes.sqlite")

httpd = socketserver.TCPServer(("", port), RequestHandler)
httpd.serve_forever()
