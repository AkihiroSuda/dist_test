#!/usr/bin/env python

import cherrypy
import dist_test
import logging
from jinja2 import Template
import simplejson

class DistTestServer(object):
  def __init__(self):
    self.config = dist_test.Config()
    self.task_queue = dist_test.TaskQueue(self.config)
    self.results_store = dist_test.ResultsStore(self.config)

  @cherrypy.expose
  def index(self):
    stats = self.task_queue.stats()
    body = "<h1>Stats</h1>\n" + self._render_stats(stats)
    recent_tasks = self.results_store.fetch_recent_task_rows()
    body += "<h1>Recent tasks</h1>\n" + self._render_tasks(recent_tasks)
    return self.render_container(body)

  @cherrypy.expose
  def job(self, job_id):
    tasks = self.results_store.fetch_task_rows_for_job(job_id)
    job_summary = self._summarize_tasks(tasks)
    success_percent = job_summary['succeeded_tasks'] * 100 / float(job_summary['total_tasks'])
    fail_percent = job_summary['failed_tasks'] * 100 / float(job_summary['total_tasks'])
    body = "<h1>Job</h1>\n"
    body += """
    <div class="progress-bar">
      <div class="filler green" style="width: %.2f%%;"></div>
      <div class="filler red" style="width: %.2f%%;"></div>
    </div>""" % (
      success_percent, fail_percent)
    body += self._render_tasks(tasks)
    return self.render_container(body)

  @cherrypy.expose
  @cherrypy.tools.json_out()
  def submit_tasks(self, job_id, tasks):
    if type(tasks) != list:
      tasks = [tasks]
    for isolate_hash in tasks:
      task = dist_test.Task.create(job_id, isolate_hash, "")
      self.results_store.register_task(task)
      self.task_queue.submit_task(task)
    return {"status": "SUCCESS"}


  @cherrypy.expose
  @cherrypy.tools.json_out()
  def submit_job(self, job_id, job_json):
    job_desc = simplejson.loads(job_json)

    for task_desc in job_desc['tasks']:
      task = dist_test.Task.create(job_id,
                                   task_desc['isolate_hash'],
                                   task_desc.get('description', ''))
      self.results_store.register_task(task)
      self.task_queue.submit_task(task)
    return {"status": "SUCCESS"}

  @cherrypy.expose
  @cherrypy.tools.json_out()
  def job_status(self, job_id):
    tasks = self.results_store.fetch_task_rows_for_job(job_id)
    job_summary = self._summarize_tasks(tasks)
    return job_summary

  def _summarize_tasks(self, tasks):
    result = {}
    result['total_tasks'] = len(tasks)
    result['finished_tasks'] = len([1 for t in tasks if t['status'] is not None])
    result['failed_tasks'] = len([1 for t in tasks if t['status'] is not None and t['status'] != 0])
    result['succeeded_tasks'] = len([1 for t in tasks if t['status'] == 0])
    return result

  def _render_stats(self, stats):
    template = Template("""
      <code>
        Queue length: {{ stats['current-jobs-ready'] }}
        Running: {{ stats['current-jobs-reserved'] }}
        Idle slaves: {{ stats['current-waiting'] }}
      </code>""")
    return template.render(stats=stats)

  def _render_tasks(self, tasks):
    for t in tasks:
      if t['stdout_abbrev']:
        t['stdout_link'] = self.results_store.generate_output_link(t, "stdout")
      if t['stderr_abbrev']:
        t['stderr_link'] = self.results_store.generate_output_link(t, "stderr")

    template = Template("""
      <script>
$(document).ready(function() {
    $('table.sortable').tablesorter();
} );
</script>
    <table class="table sortable">
    <thead>
      <tr>
        <th>submit time</th>
        <th>complete time</th>
        <th>job</th>
        <th>task</th>
        <th>description</th>
        <th>status</th>
        <th>results archive</th>
        <th>stdout</th>
        <th>stderr</th>
      </tr>
    </thead>
      {% for task in tasks %}
        <tr {% if task.status is none %}
              style="background-color: #ffa;"
            {% elif task.status == 0 %}
              style="background-color: #afa;"
            {% else %}
              style="background-color: #faa;"
            {% endif %}>
          <td>{{ task.submit_timestamp |e }}</td>
          <td>{{ task.complete_timestamp |e }}</td>
          <td><a href="/job?job_id={{ task.job_id |urlencode }}">{{ task.job_id |e }}</a></td>
          <td>{{ task.task_id |e }}</td>
          <td>{{ task.description |e }}</td>
          <td>{{ task.status |e }}</td>
          <td>{{ task.output_archive_hash |e }}</td>
          <td>{{ task.stdout_abbrev |e }}
              {% if task.stdout_link %}
              <a href="{{ task.stdout_link |e }}">download</a>
              {% endif %}
          </td>
          <td>{{ task.stderr_abbrev |e }}
              {% if task.stderr_link %}
              <a href="{{ task.stderr_link |e }}">download</a>
              {% endif %}
          </td>
        </tr>
      {% endfor %}
    </table>
    """)
    return template.render(tasks=tasks)

  def render_container(self, body):
    """ Render the "body" HTML inside of a bootstrap container page. """
    template = Template("""
    <!DOCTYPE html>
    <html>
      <head><title>Distributed Test Server</title>
      <link rel="stylesheet" href="//maxcdn.bootstrapcdn.com/bootstrap/3.2.0/css/bootstrap.min.css" />
      <style>
        .progress-bar {
          border: 1px solid #666;
          background: #eee;
          height: 30px;
          width: 80%;
          margin: auto;
          padding: 0;
          margin-bottom: 1em;
        }
        .progress-bar .filler {
           margin: 0px;
           height: 100%;
           border: 0;
           float:left;
        }
        .filler.green { background-color: #0f0; }
        .filler.red { background-color: #f00; }
      </style>
    </head>
    <body>
      <script src="//ajax.googleapis.com/ajax/libs/jquery/1.11.1/jquery.min.js"></script>
      <script src="//maxcdn.bootstrapcdn.com/bootstrap/3.2.0/js/bootstrap.min.js"></script>
      <script src="//cdnjs.cloudflare.com/ajax/libs/jquery.tablesorter/2.18.2/js/jquery.tablesorter.min.js"></script>
      <div class="container-fluid">
      {{ body }}
      </div>
    </body>
    </html>
    """)
    return template.render(body=body)


if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO)
  logging.info("hello")
  cherrypy.config.update(
    {'server.socket_host': '0.0.0.0',
     'server.socket_port': 8081})
  cherrypy.quickstart(DistTestServer())
