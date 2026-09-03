# Copyright (c) 2026 CNCKitchen (Stefan Hermann) and contributors
# SPDX-License-Identifier: AGPL-3.0-only

import http.server
import json
import os
import secrets
import shutil
import tempfile
import threading
import traceback
import urllib.parse
import uuid

import adsk.core
import adsk.fusion


COMMAND_ID = 'ExtrusionTherapyBumpMeshCommand'
COMMAND_NAME = 'BumpMesh'
COMMAND_DESCRIPTION = (
    'Texture a copy of the selected Fusion body in BumpMesh and save an STL. '
    'The editable Fusion model is not changed.'
)
PANEL_ID = 'BumpMeshFusionPanel'
LEGACY_PANEL_ID = 'ExtrusionTherapyBumpMeshPanel'
PANEL_NAME = 'BUMPMESH'
PALETTE_ID = 'ExtrusionTherapyBumpMeshPalette'
PALETTE_NAME = 'BumpMesh for Fusion'
MAX_UPLOAD_BYTES = 1_500_000_000

_handlers = []
_server = None
_server_thread = None
_server_token = None
_jobs = {}
_jobs_lock = threading.Lock()


def _web_root():
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), 'web')


def _safe_name(value):
    cleaned = ''.join(
        character if character.isalnum() or character in ('-', '_', '.') else '_'
        for character in value
    ).strip('._')
    return cleaned or 'FusionBody'


def _json_response(handler, status, payload):
    body = json.dumps(payload).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(body)


class _BridgeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=_web_root(), **kwargs)

    def log_message(self, _format, *args):
        return

    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _request_parts(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        return parsed.path, query

    def _authorized(self, query):
        supplied = query.get('token', [''])[0]
        return bool(_server_token) and secrets.compare_digest(
            supplied,
            _server_token,
        )

    def do_GET(self):
        path, query = self._request_parts()
        if path.startswith('/api/'):
            if not self._authorized(query):
                _json_response(self, 403, {'error': 'Unauthorized request.'})
                return

            pieces = [piece for piece in path.split('/') if piece]
            if len(pieces) == 4 and pieces[0] == 'api' and pieces[1] == 'jobs' and pieces[3] == 'source':
                job_id = pieces[2]
                with _jobs_lock:
                    job = _jobs.get(job_id)
                    source_path = job.get('source') if job else None
                if not source_path or not os.path.isfile(source_path):
                    _json_response(self, 404, {'error': 'Fusion export not found.'})
                    return

                file_size = os.path.getsize(source_path)
                self.send_response(200)
                self.send_header('Content-Type', 'model/stl')
                self.send_header('Content-Length', str(file_size))
                self.send_header(
                    'Content-Disposition',
                    'attachment; filename="{}"'.format(
                        os.path.basename(source_path)
                    ),
                )
                self.end_headers()
                with open(source_path, 'rb') as source_file:
                    shutil.copyfileobj(source_file, self.wfile)
                return

            _json_response(self, 404, {'error': 'Unknown API route.'})
            return

        super().do_GET()

    def do_POST(self):
        path, query = self._request_parts()
        if not self._authorized(query):
            _json_response(self, 403, {'error': 'Unauthorized request.'})
            return

        pieces = [piece for piece in path.split('/') if piece]
        if not (
            len(pieces) == 4
            and pieces[0] == 'api'
            and pieces[1] == 'jobs'
            and pieces[3] == 'output'
        ):
            _json_response(self, 404, {'error': 'Unknown API route.'})
            return

        try:
            content_length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES:
            _json_response(self, 413, {'error': 'Invalid export size.'})
            return

        job_id = pieces[2]
        requested_name = _safe_name(query.get('filename', ['textured.stl'])[0])
        extension = os.path.splitext(requested_name)[1].lower()
        if extension not in ('.stl', '.3mf'):
            _json_response(self, 400, {'error': 'Unsupported export format.'})
            return

        with _jobs_lock:
            job = _jobs.get(job_id)
        if not job:
            _json_response(self, 404, {'error': 'Unknown Fusion job.'})
            return

        output_id = uuid.uuid4().hex
        output_path = os.path.join(
            job['directory'],
            '{}{}'.format(output_id, extension),
        )
        remaining = content_length
        with open(output_path, 'wb') as output_file:
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise IOError('Export upload ended unexpectedly.')
                output_file.write(chunk)
                remaining -= len(chunk)

        with _jobs_lock:
            job['outputs'][output_id] = {
                'path': output_path,
                'filename': requested_name,
            }
        _json_response(
            self,
            200,
            {
                'jobId': job_id,
                'outputId': output_id,
                'filename': requested_name,
            },
        )


def _start_server():
    global _server, _server_thread, _server_token
    if _server:
        return _server.server_address[1]

    _server_token = secrets.token_urlsafe(32)
    _server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0),
        _BridgeHandler,
    )
    _server.daemon_threads = True
    _server_thread = threading.Thread(
        target=_server.serve_forever,
        name='BumpMeshForFusionBridge',
        daemon=True,
    )
    _server_thread.start()
    return _server.server_address[1]


def _selected_body(selection_input):
    if selection_input.selectionCount != 1:
        return None
    entity = selection_input.selection(0).entity
    body = adsk.fusion.BRepBody.cast(entity)
    if body:
        return body
    face = adsk.fusion.BRepFace.cast(entity)
    return face.body if face else None


def _export_body(body):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise RuntimeError('Open a Fusion design before launching BumpMesh.')

    job_id = uuid.uuid4().hex
    job_directory = tempfile.mkdtemp(prefix='bumpmesh-fusion-')
    source_name = '{}.stl'.format(_safe_name(body.name))
    source_path = os.path.join(job_directory, source_name)

    options = design.exportManager.createSTLExportOptions(body, source_path)
    if not options:
        raise RuntimeError('Fusion could not prepare the selected body for STL export.')
    options.sendToPrintUtility = False
    options.isBinaryFormat = True
    options.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
    options.unitType = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
    if not design.exportManager.execute(options):
        raise RuntimeError('Fusion could not export the selected body.')
    if not os.path.isfile(source_path):
        raise RuntimeError('Fusion reported success but did not create the temporary STL.')

    with _jobs_lock:
        _jobs[job_id] = {
            'directory': job_directory,
            'source': source_path,
            'outputs': {},
        }
    return job_id, source_name


def _show_palette(job_id, source_name):
    app = adsk.core.Application.get()
    ui = app.userInterface
    port = _start_server()
    query = urllib.parse.urlencode(
        {
            'fusion': '1',
            'job': job_id,
            'token': _server_token,
            'source': source_name,
        }
    )
    url = 'http://127.0.0.1:{}/?{}'.format(port, query)

    palette = ui.palettes.itemById(PALETTE_ID)
    if palette:
        palette.deleteMe()
    palette = ui.palettes.add(
        PALETTE_ID,
        PALETTE_NAME,
        url,
        True,
        True,
        True,
        760,
        760,
        True,
    )
    html_handler = _PaletteHTMLHandler()
    palette.incomingFromHTML.add(html_handler)
    _handlers.append(html_handler)
    palette.dockingOption = (
        adsk.core.PaletteDockingOptions.PaletteDockOptionsToVerticalAndHorizontal
    )
    if hasattr(palette, 'isDockedInCanvas'):
        palette.isDockedInCanvas = False
    # Docking keeps the web view inside Fusion on every monitor and uses the
    # same API on macOS and Windows. The user can resize the side panel.
    palette.dockingState = (
        adsk.core.PaletteDockingStates.PaletteDockStateRight
    )
    adsk.doEvents()


def _save_output(payload):
    app = adsk.core.Application.get()
    ui = app.userInterface
    job_id = payload.get('jobId', '')
    output_id = payload.get('outputId', '')
    with _jobs_lock:
        job = _jobs.get(job_id)
        output = job.get('outputs', {}).get(output_id) if job else None
    if not output or not os.path.isfile(output['path']):
        raise RuntimeError('The finished BumpMesh export could not be found.')

    extension = os.path.splitext(output['filename'])[1].lower()
    dialog = ui.createFileDialog()
    dialog.title = 'Save textured {}'.format(extension.upper().lstrip('.'))
    dialog.initialFilename = output['filename']
    if extension == '.3mf':
        dialog.filter = '3MF files (*.3mf)'
    else:
        dialog.filter = 'STL files (*.stl)'
    result = dialog.showSave()
    if result != adsk.core.DialogResults.DialogOK:
        return {'status': 'cancelled'}

    destination = dialog.filename
    if not destination.lower().endswith(extension):
        destination += extension
    shutil.copyfile(output['path'], destination)
    return {'status': 'saved', 'path': destination}


class _PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            if args.action == 'saveOutput':
                result = _save_output(json.loads(args.data or '{}'))
            else:
                result = {'status': 'ignored'}
            args.returnData = json.dumps(result)
        except:
            args.returnData = json.dumps(
                {
                    'status': 'error',
                    'message': traceback.format_exc(),
                }
            )


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, selection_input):
        super().__init__()
        self.selection_input = selection_input

    def notify(self, _args):
        ui = adsk.core.Application.get().userInterface
        try:
            body = _selected_body(self.selection_input)
            if not body:
                raise RuntimeError('Select one solid body to send to BumpMesh.')
            job_id, source_name = _export_body(body)
            _show_palette(job_id, source_name)
        except:
            ui.messageBox('BumpMesh failed:\n{}'.format(traceback.format_exc()))


class _CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        inputs = args.command.commandInputs
        notice = inputs.addTextBoxCommandInput(
            'sourceNotice',
            '',
            'Your editable Fusion model will not be changed.',
            1,
            True,
        )
        notice.isFullWidth = True
        selection_input = inputs.addSelectionInput(
            'bodySelection',
            'Body',
            'Select one solid body to texture.',
        )
        selection_input.addSelectionFilter('SolidBodies')
        selection_input.setSelectionLimits(1, 1)

        ui = adsk.core.Application.get().userInterface
        if ui.activeSelections.count:
            entity = ui.activeSelections.item(0).entity
            body = adsk.fusion.BRepBody.cast(entity)
            face = adsk.fusion.BRepFace.cast(entity)
            candidate = body or (face.body if face else None)
            if candidate:
                selection_input.addSelection(candidate)

        execute_handler = _ExecuteHandler(selection_input)
        args.command.execute.add(execute_handler)
        _handlers.append(execute_handler)


def run(_context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        _start_server()
        definition = ui.commandDefinitions.itemById(COMMAND_ID)
        if not definition:
            definition = ui.commandDefinitions.addButtonDefinition(
                COMMAND_ID,
                COMMAND_NAME,
                COMMAND_DESCRIPTION,
                os.path.join(os.path.dirname(__file__), 'resources'),
            )

        created_handler = _CommandCreatedHandler()
        definition.commandCreated.add(created_handler)
        _handlers.append(created_handler)

        workspace = ui.workspaces.itemById('FusionSolidEnvironment')
        tab = workspace.toolbarTabs.itemById('SolidTab')
        legacy_panel = tab.toolbarPanels.itemById(LEGACY_PANEL_ID)
        if legacy_panel:
            legacy_panel.deleteMe()
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME)
        else:
            panel.name = PANEL_NAME
        control = panel.controls.itemById(COMMAND_ID)
        if not control:
            control = panel.controls.addCommand(definition)
        control.isVisible = True
        control.isPromotedByDefault = True
        control.isPromoted = True
        adsk.autoTerminate(False)
    except:
        ui.messageBox('BumpMesh setup failed:\n{}'.format(traceback.format_exc()))


def stop(_context):
    global _server, _server_thread, _server_token
    app = adsk.core.Application.get()
    ui = app.userInterface

    palette = ui.palettes.itemById(PALETTE_ID)
    if palette:
        palette.deleteMe()

    workspace = ui.workspaces.itemById('FusionSolidEnvironment')
    if workspace:
        tab = workspace.toolbarTabs.itemById('SolidTab')
        if tab:
            panel = tab.toolbarPanels.itemById(PANEL_ID)
            if panel:
                panel.deleteMe()
            legacy_panel = tab.toolbarPanels.itemById(LEGACY_PANEL_ID)
            if legacy_panel:
                legacy_panel.deleteMe()

    definition = ui.commandDefinitions.itemById(COMMAND_ID)
    if definition:
        definition.deleteMe()

    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
    if _server_thread:
        _server_thread.join(timeout=2)
        _server_thread = None
    _server_token = None

    with _jobs_lock:
        jobs = list(_jobs.values())
        _jobs.clear()
    for job in jobs:
        shutil.rmtree(job['directory'], ignore_errors=True)

    _handlers.clear()
