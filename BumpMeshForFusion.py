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
import html
from contextlib import contextmanager

import adsk.core
import adsk.fusion

from . import updates


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
PALETTE_WIDTH_FRACTION = 0.48
PALETTE_FALLBACK_WIDTH = 760
PALETTE_FALLBACK_HEIGHT = 760
MAX_UPLOAD_BYTES = 1_500_000_000
UPDATE_COMMAND_ID = 'BumpMeshCheckForUpdates'
UPDATE_EVENT_ID = 'BumpMeshUpdateResult'

_handlers = []
_server = None
_server_thread = None
_server_token = None
_jobs = {}
_jobs_lock = threading.Lock()
_temporary_directory = None
_updater = None
_update_command = None
_update_result = None
_palette_handlers = []


@contextmanager
def _job_access(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job and not job.get('retired'):
            job['users'] = job.get('users', 0) + 1
        else:
            job = None
    try:
        yield job
    finally:
        if job:
            with _jobs_lock:
                job['users'] -= 1
            _clean_retired_jobs()


def _clean_retired_jobs(keep=None):
    global _temporary_directory
    with _jobs_lock:
        for key, job in list(_jobs.items()):
            if keep is not None and key != keep:
                job['retired'] = True
            if job.get('retired') and not job.get('users', 0):
                try:
                    shutil.rmtree(job['directory'])
                except OSError:
                    continue  # Retry on the next transition or at shutdown.
                del _jobs[key]
        if not _jobs and _server is None and _temporary_directory is not None:
            try:
                _temporary_directory.cleanup()
            except OSError:
                return
            _temporary_directory = None


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
            supplied.encode('utf-8'),
            _server_token.encode('utf-8'),
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
                with _job_access(job_id) as job:
                    source_path = job.get('source') if job else None
                    if not source_path or not os.path.isfile(source_path):
                        _json_response(self, 404, {'error': 'Fusion export not found.'})
                        return
                    with open(source_path, 'rb') as source_file:
                        self.send_response(200)
                        self.send_header('Content-Type', 'model/stl')
                        self.send_header('Content-Length', str(os.fstat(source_file.fileno()).st_size))
                        self.end_headers()
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

        with _job_access(job_id) as job:
            if not job:
                _json_response(self, 404, {'error': 'Unknown Fusion job.'})
                return
            output_id = uuid.uuid4().hex
            output_path = os.path.join(job['directory'], output_id + extension)
            try:
                self.connection.settimeout(60)
                remaining = content_length
                with open(output_path, 'wb') as output_file:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise IOError('Export upload ended unexpectedly.')
                        output_file.write(chunk)
                        remaining -= len(chunk)
                with _jobs_lock:
                    job['outputs'][output_id] = {'path': output_path, 'filename': requested_name}
                _json_response(self, 200, {'jobId': job_id, 'outputId': output_id,
                                           'filename': requested_name})
            except OSError:
                with _jobs_lock:
                    job['outputs'].pop(output_id, None)
                if os.path.exists(output_path):
                    os.remove(output_path)
                try:
                    _json_response(self, 400, {'error': 'Upload interrupted. Export again to retry.'})
                except OSError:
                    pass


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
    global _temporary_directory
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise RuntimeError('Open a Fusion design before launching BumpMesh.')

    job_id = uuid.uuid4().hex
    if _temporary_directory is None:
        _temporary_directory = tempfile.TemporaryDirectory(prefix='bumpmesh-fusion-')
    job_directory = tempfile.mkdtemp(dir=_temporary_directory.name)
    source_name = '{}.stl'.format(_safe_name(body.name))
    source_path = os.path.join(job_directory, source_name)

    try:
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
    except Exception:
        shutil.rmtree(job_directory, ignore_errors=True)
        raise

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
        adsk.doEvents()
    _palette_handlers.clear()

    viewport = app.activeViewport
    if viewport:
        palette_width = max(1, round(viewport.width * PALETTE_WIDTH_FRACTION))
        palette_height = max(1, viewport.height)
    else:
        palette_width = PALETTE_FALLBACK_WIDTH
        palette_height = PALETTE_FALLBACK_HEIGHT

    palette = ui.palettes.add(
        PALETTE_ID,
        PALETTE_NAME,
        url,
        True,
        True,
        True,
        palette_width,
        palette_height,
        True,
    )
    html_handler = _PaletteHTMLHandler()
    palette.incomingFromHTML.add(html_handler)
    _palette_handlers.append(html_handler)
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
    palette.setSize(palette_width, palette_height)
    _clean_retired_jobs(keep=job_id)
    adsk.doEvents()


def _save_output(payload):
    with _job_access(payload.get('jobId', '')) as job:
        output_id = payload.get('outputId', '')
        output = job.get('outputs', {}).get(output_id) if job else None
        if not output or not os.path.isfile(output['path']):
            raise RuntimeError('The finished export could not be found. Export again to retry.')
        try:
            return _save_file(output)
        finally:
            # Retrying Export produces a fresh output. Never retain abandoned staging copies.
            with _jobs_lock:
                job['outputs'].pop(output_id, None)
            try:
                os.remove(output['path'])
            except OSError:
                pass


def _save_file(output):
    app = adsk.core.Application.get()
    ui = app.userInterface
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
    fd, staged = tempfile.mkstemp(prefix='.bumpmesh-', dir=os.path.dirname(destination))
    os.close(fd)
    try:
        shutil.copyfile(output['path'], staged)
        os.replace(staged, destination)
    finally:
        if os.path.exists(staged):
            os.remove(staged)
    return {'status': 'saved', 'path': destination}


class _PaletteHTMLHandler(adsk.core.HTMLEventHandler):
    def notify(self, args):
        try:
            if args.action == 'saveOutput':
                result = _save_output(json.loads(args.data or '{}'))
            else:
                result = {'status': 'ignored'}
            args.returnData = json.dumps(result)
        except Exception as error:
            args.returnData = json.dumps(
                {
                    'status': 'error',
                    'message': 'Could not save the export. {}. Export again to retry.'.format(str(error)),
                }
            )


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, selection_input):
        super().__init__()
        self.selection_input = selection_input

    def notify(self, _args):
        ui = adsk.core.Application.get().userInterface
        job_id = None
        try:
            body = _selected_body(self.selection_input)
            if not body:
                raise RuntimeError('Select one solid body to send to BumpMesh.')
            job_id, source_name = _export_body(body)
            _show_palette(job_id, source_name)
        except:
            if job_id:
                with _jobs_lock:
                    _jobs[job_id]['retired'] = True
                _clean_retired_jobs()
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
        destroyed = _ReleaseHandlers([execute_handler])
        args.command.destroy.add(destroyed)
        _handlers.extend([execute_handler, destroyed])


class _ReleaseHandlers(adsk.core.CommandEventHandler):
    def __init__(self, handlers):
        super().__init__()
        self.handlers = handlers

    def notify(self, _args):
        for handler in self.handlers + [self]:
            if handler in _handlers:
                _handlers.remove(handler)


def _update_text(result):
    if result['status'] == 'available':
        return ('Version {} is available. <a href="{}">Download installer</a>.'
                '<br>Installation is manual. Close Fusion before running the installer.').format(
                    html.escape(result['version']), html.escape(result['url'], quote=True))
    if result['status'] == 'current':
        return 'Your installed version is up to date. No newer compatible stable release is available.'
    return html.escape(result['message'])


class _UpdateResultHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        global _update_result
        payload = json.loads(args.additionalInfo)
        result = payload['result']
        if _updater is None:
            return
        if _update_command is not None and _update_command.isValid:
            _update_result = result
            _update_command.commandInputs.itemById('updateStatus').formattedText = _update_text(result)
        if result['status'] == 'available':
            definition = adsk.core.Application.get().userInterface.commandDefinitions.itemById(UPDATE_COMMAND_ID)
            if definition:
                # Quiet notification in the existing menu, never a modal startup prompt.
                definition.name = 'Check for Updates (update available)'
                definition.tooltip = 'BumpMesh for Fusion {} is available.'.format(result['version'])


class _UpdateExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, command):
        super().__init__()
        self.command = command

    def notify(self, _args):
        automatic = self.command.commandInputs.itemById('automaticUpdates').value
        dismissed = _update_result['version'] if _update_result and _update_result['status'] == 'available' else None
        try:
            _updater.configure(automatic, dismissed)
        except OSError:
            adsk.core.Application.get().userInterface.messageBox('Could not save update preferences. Please try again.')
            return
        definition = adsk.core.Application.get().userInterface.commandDefinitions.itemById(UPDATE_COMMAND_ID)
        if definition:
            definition.name = 'Check for Updates'


class _UpdateDestroyedHandler(_ReleaseHandlers):
    def notify(self, _args):
        global _update_command, _update_result
        _update_command, _update_result = None, None
        super().notify(_args)


class _UpdateCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _update_command, _update_result
        _update_command, _update_result = args.command, None
        args.command.okButtonText = 'Done'
        inputs = args.command.commandInputs
        status = inputs.addTextBoxCommandInput('updateStatus', '', 'Checking for updates…', 4, True)
        status.isFullWidth = True
        inputs.addBoolValueInput('automaticUpdates', 'Check automatically every week', True, '', _updater.state['automatic'])
        execute = _UpdateExecuteHandler(args.command)
        destroyed = _UpdateDestroyedHandler([execute])
        args.command.execute.add(execute)
        args.command.destroy.add(destroyed)
        _handlers.extend([execute, destroyed])
        _updater.request()


def run(_context):
    global _updater
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
        update_definition = ui.commandDefinitions.itemById(UPDATE_COMMAND_ID)
        if not update_definition:
            update_definition = ui.commandDefinitions.addButtonDefinition(
                UPDATE_COMMAND_ID, 'Check for Updates', 'Check for a newer BumpMesh for Fusion installer.')
        update_created = _UpdateCreatedHandler()
        update_definition.commandCreated.add(update_created)
        _handlers.append(update_created)
        if not panel.controls.itemById(UPDATE_COMMAND_ID):
            panel.controls.addCommand(update_definition)
        event = app.registerCustomEvent(UPDATE_EVENT_ID)
        update_handler = _UpdateResultHandler()
        event.add(update_handler)
        _handlers.append(update_handler)
        with open(os.path.join(os.path.dirname(__file__), 'BumpMeshForFusion.manifest'), encoding='utf-8') as handle:
            installed_version = json.load(handle)['version']
        _updater = updates.UpdateChecker(installed_version, lambda result, manual:
            app.fireCustomEvent(UPDATE_EVENT_ID, json.dumps({'result': result, 'manual': manual})))
        _updater.start()
        adsk.autoTerminate(False)
    except:
        ui.messageBox('BumpMesh setup failed:\n{}'.format(traceback.format_exc()))


def stop(_context):
    global _server, _server_thread, _server_token, _temporary_directory, _updater, _update_command, _update_result
    app = adsk.core.Application.get()
    ui = app.userInterface
    if _updater:
        _updater.stop()
        _updater = None
        app.unregisterCustomEvent(UPDATE_EVENT_ID)
    _update_command, _update_result = None, None

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
    update_definition = ui.commandDefinitions.itemById(UPDATE_COMMAND_ID)
    if update_definition:
        update_definition.deleteMe()

    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
    if _server_thread:
        _server_thread.join(timeout=2)
        _server_thread = None
    _server_token = None

    with _jobs_lock:
        for job in _jobs.values():
            job['retired'] = True
    # Requests already reading/writing keep their job until their finalizer runs.
    _clean_retired_jobs()

    _handlers.clear()
    _palette_handlers.clear()
