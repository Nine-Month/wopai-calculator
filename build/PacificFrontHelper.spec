# -*- mode: python ; coding: utf-8 -*-
# Slim onedir build: no --collect-all PySide6 (avoids WebEngine/QML/etc.).
# Paths relative to this file (build/).
block_cipher = None

a = Analysis(
    ['../main.py'],
    pathex=['..'],
    binaries=[],
    datas=[
        ('../bridge/inject.js', 'bridge'),
        ('../data/locale_zh_sg.json', 'data'),
        ('../data/mod_icons', 'data/mod_icons'),
        ('../assets/app-icon.ico', 'assets'),
    ],
    hiddenimports=[
        'aiohttp', 'multidict', 'yarl', 'frozenlist', 'aiosignal', 'attr',
        'PySide6', 'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
        'shiboken6',
        'core', 'core.solver', 'core.items_solve', 'core.scrape',
        'core.buff_cache', 'core.resource_summary', 'core.auto_plan',
        'app', 'app.ui', 'app.bridge', 'app.worker', 'app.auto_session',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        '_tkinter',
        'turtle',
        'test',
        'unittest',
        'pdb',
        'doctest',
        'pydoc',
        'idlelib',
        'lib2to3',
        'ensurepip',
        'venv',
        'virtualenv',
        'numpy',
        'pandas',
        'matplotlib',
        'scipy',
        'PIL',
        'cv2',
        'IPython',
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.QtQuick',
        'PySide6.QtQuickControls2',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickTest',
        'PySide6.QtQml',
        'PySide6.QtQmlModels',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DExtras',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtGraphs',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtSpatialAudio',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtSensors',
        'PySide6.QtSerialPort',
        'PySide6.QtSerialBus',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtTextToSpeech',
        'PySide6.QtDesigner',
        'PySide6.QtUiTools',
        'PySide6.QtHelp',
        'PySide6.QtTest',
        'PySide6.QtRemoteObjects',
        'PySide6.QtHttpServer',
        'PySide6.QtNetworkAuth',
        'PySide6.QtStateMachine',
        'PySide6.QtScxml',
        'PySide6.QtSvgWidgets'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Drop leftover heavy Qt binaries/resources that hooks may still pull
_DROP_SUBSTR = (
    'WebEngine', 'webengine', 'Qt6Quick', 'Qt6Qml', 'Qt6Pdf', 'Qt6Designer',
    'Qt63D', 'Qt6Charts', 'Qt6DataVisualization', 'Qt6Graphs', 'Qt6Multimedia',
    'Qt6Bluetooth', 'Qt6Nfc', 'Qt6Positioning', 'Qt6Location', 'Qt6Sensors',
    'Qt6Serial', 'Qt6TextToSpeech', 'Qt6RemoteObjects', 'Qt6ShaderTools',
    'Qt6Quick3D', 'Qt6Labs', 'qmlls', 'qmlformat', 'assistant.exe',
    'linguist.exe', 'designer.exe', 'avcodec', 'avformat', 'avutil', 'swresample',
    'swscale', 'opengl32sw', 'icudtl.dat', 'v8_context', 'qtwebengine',
)

def _keep(item):
    # item is TOC tuple (name, path, typecode) or similar
    name = item[0] if isinstance(item, (tuple, list)) else str(item)
    low = name.replace('\\', '/').lower()
    for s in _DROP_SUBSTR:
        if s.lower() in low:
            return False
    # drop debug pdbs
    if low.endswith('.pdb'):
        return False
    return True

a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PacificFrontHelper',
    icon='../assets/app-icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='PacificFrontHelper',
)
