import http.server
import socketserver
from urllib.parse import urlparse, parse_qs, unquote_plus

import sqlite3
import json
import sys
import os

from raytracer import *

class RequestHandler(http.server.SimpleHTTPRequestHandler):
  static_dir = '/client'

  def do_GET(self):
    self.init_params()

    if len(self.path_info) > 0 and self.path_info[0] == "service":
      if len(self.path_info) < 2:
        self.send_error(400)
        
      elif self.path_info[1] == '':
        self.send_error(404)

      elif self.path_info[1] == 'image':
        if ( len(self.path_info) < 3 or self.path_info[2] == ''):
          accept = self.headers.get('Accept')
          # html
          if accept == 'text/html':
            cursor = conn.cursor()
            data = self.sql_fetchall(cursor,"SELECT name, filename FROM scene")
            options = []
            for scene in data:
              scene = self.add_url(scene)
              options.append('<option value="{}">{}</option>'.format(scene['url'],scene['name']))
            self.send_html("\n".join(options))
            cursor.close()
          else:
            self.send_error(406)
        else:
          self.send_error(404)

      elif self.path_info[1] == 'scene':
        if ( len(self.path_info) < 3 or self.path_info[2] == ''):
          cursor = conn.cursor()
          accept = self.headers.get('Accept')
          # html
          if accept == 'text/html':
            data = self.sql_fetchall(cursor,"SELECT id, name FROM scene")
            options = []
            for scene in data:
              options.append('<option value="{}">{}</option>'.format(scene['id'],scene['name']))
            self.send_html("\n".join(options))
            cursor.close()
          # json
          else:
            data = self.sql_fetchall(cursor,"SELECT * FROM scene")
            data = [self.add_url(row) for row in data]
            self.send_json(data) if data else self.send_error(404,'Empty table')
            cursor.close()

        # GET /service/scene/%id
        else:
          scene_id = self.path_info[2]
          cursor = conn.cursor()
          data = self.sql_fetchone(cursor,"SELECT * FROM scene WHERE id = ? OR name = ?",(scene_id,scene_id))
          data = self.add_url(data)
          self.send_json(data) if data else self.send_error(404,'No such scene')
          cursor.close()

      # GET /service/*
      else:
        self.send_error(404)

    # GET * - requête soumise au serveur de documents statiques
    else:
      self.send_static()


  def do_HEAD(self):
    self.send_static()


  def do_POST(self):
    self.init_params()

    # POST /service...
    if self.path_info[0] == "service":

      # POST /service
      if len(self.path_info) < 2:
        self.send_error(400)
        return

      # POST /service/scene...
      if self.path_info[1] == "scene":

        # POST /service/scene
        if ( len(self.path_info) < 3 or self.path_info[2] == ''):

          # on renvoie une erreur s'il manque name ou serial
          if not 'name' in self.params or not 'serial' in self.params:
             self.send_error(400)
             return

          # on construit un dictionnaire avec les paramètres reçus
          info = { k: self.params[k] for k in ('name','serial') }
          for k in ('width', 'height', 'ptime', 'filename'):
            if k in self.params:
              info[k] = self.params[k]

          # on soumet la requête INSERT
          keys = info.keys()
          params = list(info.values())
          sql = "INSERT INTO scene ({}) VALUES({})".format(','.join(keys),','.join(['?']*len(keys)))
          try:
            cursor = conn.cursor()
            cursor.execute(sql,params)

          # problème de duplication du nom
          except sqlite3.IntegrityError as e:
            self.send_error(400,str(e))
            #self.send_error(400,'Duplicate Name')
            cursor.close()
            return

          # on génère l'image si on a des dimensions
          if 'width' in info and not int(info['width']) == 0 and \
             'height' in info and not int(info['height']) == 0: 

            # nom de fichier
            filename = info['filename'] if 'filename' in info else ''
            scene = self.create_image(info['name'],info['width'], info['height'], info['serial'], filename)
            filename = scene.filename

            # on met à jour le temps de calcul et le nom de fichier dans la base
            cursor.execute(
              "UPDATE scene SET ptime = ?, filename = ? WHERE name = ?",
              (scene.ptime, filename, info['name']))
            if not cursor.rowcount:
              self.error(500,'Could not update scene record')
              conn.commit()
              cursor.close()
              return

          # on renvoie la nouvelle scène avec tous ses champs
          data = self.sql_fetchone(cursor,"SELECT * FROM scene WHERE name = ?",(info['name'],))
          data = self.add_url(data)
          self.send_json(data) if data else self.send_error(500,'Could not refetch scene record')
          conn.commit()
          cursor.close()

        # POST /service/scene/%id
        else:
          scene_id = self.path_info[2]
          info = json.loads(self.body)
          filename = ''

          # delete
          if "operation" in info and info['operation'] == "delete":
            cursor = conn.cursor()

            # récupération du nom de fichier
            if "delete_file" in info and info['delete_file']:
              data = self.select_one(cursor,
                "SELECT filename FROM scene WHERE id = ? OR name = ?",(scene_id,scene_id))
              if not data:
                self.send_error(404)
                cursor.close()
                return
              filename = data['filename']

            # suppression de l'enregistrement
            cursor.execute("DELETE FROM scene WHERE id = ? OR NAME = ?",(scene_id,scene_id))
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

          # update
          if "operation" in info and info['operation'] == "update":
            cursor = conn.cursor()
            data = self.sql_fetchone(cursor,
              "SELECT name, width, height, serial, filename FROM scene WHERE id = ? OR name = ?",
              (scene_id,scene_id))
            if not data:
              self.send_error(404)
              cursor.close()
              return
            (name, width, height, serial, filename) = [data[k] for k in data]

            # il faut recalculer l'image
            if (('width' in info and info['width'] and not info['width'] == width) or \
                ('height' in info and info['height'] and not info['height'] == height) or \
                ('serial' in info and info['serial'] and not info['serial'] == serial) or \
                ('filename' in info and info['filename'] and not filename)) and not \
                ('filename' in info and not info['filename']):
              try:
                if not 'filename' in info:
                  info['filename'] = filename
                scene = self.create_image( name,
                  info['width'] if 'width' in info else width,
                  info['height'] if 'height' in info else height,
                  info['serial'] if 'serial' in info else serial,
                  info['filename'])
              except ValueError as e:
                self.send_error(400,str(e))
                cursor.close()
                return

              info['filename'] = scene.filename
              info['ptime'] = scene.ptime

            # il faut supprimer l'image
            if ('filename' in info and not info['filename']) or \
               ('width' in info and not info['width']) or \
               ('height' in info and not info['height']) or \
               ('serial' in info and not info['serial']):
              if os.path.isfile(filename):
                os.remove(filename)
                info['ptime'] = 0

            # il faut déplacer l'image
            if 'filename' in info and info['filename'] and not info['filename'] == filename:
              if os.path.isfile(filename):
                os.rename(filename,info['filename'])

            # préparation des modifications demandées, pour construction de la requête SQL
            sqlset = []
            params = []
            for k in ('serial', 'width', 'height', 'ptime', 'filename'):
              if k in info:
                sqlset.append("{} = ?".format(k))
                params.append(info[k])

            # il y a des infos à mettre à jour
            if ( len(params) ):
              params.append(scene_id)
              params.append(scene_id)
              sql = "UPDATE scene SET {} WHERE id = ? OR name = ?".format(', '.join(sqlset)) 
              cursor.execute(sql,params)

              # problème
              if not cursor.rowcount:
                self.send_error(500,'Could not update scene')
                cursor.close()
                return

              # Récupération de la scène à jour
              data = self.sql_fetchone(cursor,
                "SELECT * FROM scene WHERE id = ? OR NAME = ?",(scene_id,scene_id))
              data = self.add_url(data)
              self.send_json(data) if data else self.send_error(500,'Could not refetch scene')

              conn.commit()
              cursor.close()
              return

            # il n'y a pas d'infos à mettre à jour
            else:
              self.send_error(400, 'Missing Fields')

          # unkown operation
          else:
            self.send_error(400,"Unkown Operation")

      else:
        self.send_error(400,'Unknown Service')

    # méthode non autorisée
    else:
      self.send_error(405)


  # on analyse la requête pour récupérer les paramètres
  def init_params(self):
    
    # analyse de l'adresse
    info = urlparse(unquote_plus(self.path))
    self.path_info = info.path.split('/')[1:]
    self.query_string = info.query
    self.params = parse_qs(info.query)

    # récupération du corps
    length = self.headers.get('Content-Length')
    ctype = self.headers.get('Content-Type')
    if length:
      self.body = str(self.rfile.read(int(length)),'utf-8')
      if ctype == 'application/json' : 
        self.params.update(json.loads(self.body))
    else:
        self.body = ''

  # on envoie un contenu encodé en html
  def send_html(self, html, headers=[]):
    body = bytes(html,'utf-8')
    headers.append(('Content-Type','text/html'))
    self.send_utf8(body,headers)

  # on envoie un contenu encodé en json
  def send_json(self, data, headers=[]):
    body = bytes(json.dumps(data),'utf-8')
    headers.append(('Content-Type','application/json'))
    self.send_utf8(body,headers)

  def send_utf8(self, encoded, headers=[]):
    self.send_response(200)
    self.send_header('Content-Length',int(len(encoded)))
    self.send_header('Access-Control-Allow-Origin','*')
    [self.send_header(*t) for t in headers]
    self.end_headers()
    self.wfile.write(encoded)


  # on envoie le document statique demandé
  def send_static(self):

    # on modifie le chemin d'accès en insérant le répertoire préfixe
    self.path = self.static_dir + self.path

    # on calcule le nom de la méthode (do_GET ou do_HEAD) à appeler
    # via la classe mère, à partir du verbe HTTP (GET ou HEAD)
    method = 'do_{}'.format(self.command)

    # on traite la requête via la classe mère
    getattr(http.server.SimpleHTTPRequestHandler,method)(self)


  # Récupération du resultat d'une requête SQL
  def sql_fetchall(self, cursor, sql, params=[]):
    cursor.execute(sql,params)
    result = cursor.fetchall()
    desc = cursor.description
    return [{desc[k][0]: row[k] for k in range(len(desc))} for row in result] if result else None

  def sql_fetchone(self, cursor, sql, params=[]):
    cursor.execute(sql,params)
    result = cursor.fetchone()
    desc = cursor.description
    return {desc[k][0]: result[k] for k in range(len(desc))} if result else None

  # Création d'une image
  def create_image(self, name, width, height, serial, filename):
    if not filename:
      filename = 'client/images/{}.png'.format(name)
    scene = Scene.deserialize(serial)
    scene.initialize(width, height)
    scene.trace(True)
    scene.save_image(filename)
    return scene

  # On ajoute l'url aux informations d'une scène
  def add_url(self, data):
    if 'filename' in data and data['filename'][:7] == 'client/':
      data['url'] = data['filename'].replace('client','',1)
    return data


PORT = 8080
print('Running on port {}'.format(PORT))

conn = sqlite3.connect('rtserver.sqlite')

httpd = socketserver.TCPServer(("", PORT), RequestHandler)
httpd.serve_forever()

