"""Native, local NIF material inspection panel. No WebEngine or added packages."""
from __future__ import annotations
import math
import threading
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QImage, QMatrix4x4, QVector3D, QVector4D, QSurfaceFormat
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QLineEdit, QFileDialog, QComboBox, QCheckBox, QPlainTextEdit)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtOpenGL import QOpenGLShaderProgram, QOpenGLShader, QOpenGLBuffer, QOpenGLTexture
import i18n
from nif_preview_data import load_scene

VERTEX_SHADER = '''
#version 120
attribute vec3 aPosition;
attribute vec3 aNormal;
attribute vec2 aUV;
attribute vec4 aColor;
uniform mat4 uMVP;
uniform mat4 uMV;
uniform vec4 uUV;
varying vec3 vPosition;
varying vec3 vNormal;
varying vec2 vUV;
varying vec4 vColor;
void main() {
  gl_Position = uMVP * vec4(aPosition, 1.0);
  vPosition = (uMV * vec4(aPosition, 1.0)).xyz;
  vNormal = normalize((uMV * vec4(aNormal, 0.0)).xyz);
  vUV = aUV * uUV.zw + uUV.xy;
  vColor = aColor;
}
'''

FRAGMENT_SHADER = '''
#version 120
uniform sampler2D uBase;
uniform sampler2D uNormal;
uniform sampler2D uSpec;
uniform sampler2D uGlow;
uniform vec4 uMaps;
uniform vec4 uTint;
uniform vec4 uSpecParams;
uniform vec3 uSpecColor;
uniform vec3 uEmittance;
uniform int uMode;
uniform int uVertexColor;
uniform int uModelSpace;
uniform int uUseAlpha;
uniform float uAlphaTest;
uniform mat4 uMV;
varying vec3 vPosition;
varying vec3 vNormal;
varying vec2 vUV;
varying vec4 vColor;
vec3 linearize(vec3 v) { return pow(max(v, vec3(0.0)), vec3(2.2)); }
void main() {
  vec4 base = uMaps.x > 0.5 ? texture2D(uBase, vUV) : vec4(0.65, 0.67, 0.69, 1.0);
  base *= uTint;
  if (uVertexColor != 0) base *= vColor;
  if (uUseAlpha == 0) base.a = 1.0;
  if (base.a < uAlphaTest) discard;
  vec3 N = normalize(vNormal);
  if (!gl_FrontFacing) N = -N;
  if (uMaps.y > 0.5) {
    vec3 map = texture2D(uNormal, vUV).xyz * 2.0 - 1.0;
    if (uModelSpace != 0) {
      N = normalize((uMV * vec4(map, 0.0)).xyz);
    } else {
      map.z = sqrt(max(1.0 - dot(map.xy, map.xy), 0.001));
      vec3 dp1 = dFdx(vPosition), dp2 = dFdy(vPosition);
      vec2 duv1 = dFdx(vUV), duv2 = dFdy(vUV);
      vec3 dp2perp = cross(dp2, N), dp1perp = cross(N, dp1);
      vec3 T = dp2perp * duv1.x + dp1perp * duv2.x;
      vec3 B = dp2perp * duv1.y + dp1perp * duv2.y;
      float magnitude = max(dot(T,T), dot(B,B));
      if (magnitude > 0.00000001) N = normalize(mat3(T * inversesqrt(magnitude), B * inversesqrt(magnitude), N) * map);
    }
  }
  if (uMode == 1) { gl_FragColor = base; return; }
  if (uMode == 2) { gl_FragColor = vec4(N * 0.5 + 0.5, 1.0); return; }
  vec3 L = normalize(vec3(-0.6, 0.8, 1.3));
  vec3 fill = normalize(vec3(0.8, -0.2, 0.7));
  vec3 V = normalize(-vPosition), H = normalize(L + V);
  vec3 diffuse = linearize(base.rgb) * (0.22 + 0.70 * max(dot(N,L),0.0) + 0.18 * max(dot(N,fill),0.0));
  vec2 spec = uMaps.z > 0.5 ? texture2D(uSpec,vUV).rg : vec2(0.2, 0.35);
  float strength = spec.x * uSpecParams.x;
  float smoothness = clamp(spec.y * uSpecParams.y, 0.0, 1.0);
  float exponent = 2.0 + 160.0 * pow(smoothness,4.0);
  float highlight = pow(max(dot(N,H),0.0), exponent) * strength;
  float fresnel = pow(1.0-max(dot(N,V),0.0), 5.0) * strength * 0.18;
  vec3 glow = uMaps.w > 0.5 ? linearize(texture2D(uGlow,vUV).rgb) * uEmittance : vec3(0.0);
  vec3 color = diffuse + uSpecColor * (highlight + fresnel) + glow;
  gl_FragColor = vec4(pow(clamp(color,0.0,1.0),vec3(1.0/2.2)), base.a);
}
'''

def _text(zh, en):
    return zh if i18n.LANG == 'zh' else en

def _ghost_button(button, compact=False):
    button.setObjectName('ghost')
    button.setMinimumHeight(36 if compact else 32)
    padding = '0px' if compact else '5px 12px'
    button.setStyleSheet("QPushButton#ghost { background:#ffffff; color:#24241f; "
                        "border:1px solid #e1dfd6; border-radius:14px; padding:" + padding + "; letter-spacing:0px; } "
                        "QPushButton#ghost:hover { background:#f1efe7; border-color:#ff4b23; } "
                        "QPushButton#ghost:pressed, QPushButton#ghost:checked { background:#eeeae0; } "
                        "QPushButton#ghost:disabled { background:#f8f7f2; color:#aaa79e; border-color:#eceae3; }")
    if compact: button.setFixedWidth(40)

class LoadWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)
    def __init__(self, path, converted, source, parent=None):
        super().__init__(parent)
        self.args = (path, converted, source)
        self.cancel_event = threading.Event()
    def run(self):
        try:
            scene = load_scene(*self.args, cancel=self.cancel_event)
            if not self.cancel_event.is_set(): self.loaded.emit(scene)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.cancel_event.is_set(): self.failed.emit(str(exc))

class NifViewport(QOpenGLWidget):
    render_error = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        format = QSurfaceFormat()
        format.setVersion(2, 1)
        format.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        format.setDepthBufferSize(24)
        format.setSamples(4)
        self.setFormat(format)
        self.setMinimumSize(320, 220)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scene = None
        self._program = None
        self._gpu = []
        self._textures = {}
        self._pending_upload = False
        self._error = ''
        self._yaw, self._pitch, self._zoom = 158., -10., 1.
        self._last = None
        self.mode, self.vertex_colors = 0, False
    def sizeHint(self): return QSize(850, 430)
    def set_scene(self, scene):
        self.scene = scene
        self._error = ''
        self._pending_upload = True
        self.fit()
    def fit(self):
        self._yaw, self._pitch, self._zoom = 158., -10., 1.
        self.update()
    def set_mode(self, mode): self.mode = mode; self.update()
    def set_vertex_colors(self, enabled): self.vertex_colors = enabled; self.update()
    def initializeGL(self):
        try:
            self._functions = self.context().functions()
            self._functions.initializeOpenGLFunctions()
            program = QOpenGLShaderProgram(self)
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER):
                raise RuntimeError(program.log())
            if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, FRAGMENT_SHADER):
                raise RuntimeError(program.log())
            if not program.link(): raise RuntimeError(program.log())
            self._program = program
            self.context().aboutToBeDestroyed.connect(self.release_gl)
            self._pending_upload = self.scene is not None
        except Exception as exc:
            self._error = 'OpenGL preview unavailable: ' + str(exc)
            self.render_error.emit(self._error)
    def _clear_resources(self):
        for buffer, _, _, _ in self._gpu: buffer.destroy()
        self._gpu.clear()
        for texture in self._textures.values(): texture.destroy()
        self._textures.clear()
    def release_gl(self):
        if self.context() is None: return
        self.makeCurrent()
        self._clear_resources()
        if self._program is not None:
            self._program.removeAllShaders()
            self._program = None
        self.doneCurrent()
    def _upload(self):
        self._clear_resources()
        if self.scene is None:
            self._pending_upload = False
            return
        for mesh in self.scene.meshes:
            buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            if not buffer.create(): raise RuntimeError('Could not create vertex buffer')
            buffer.bind()
            raw = mesh.vertices.tobytes()
            buffer.allocate(raw, len(raw)); buffer.release()
            fields = mesh.material.fields
            transparent = bool(fields.get('AlphaBlend0')) or float(fields.get('Alpha',1.0)) < 1
            self._gpu.append((buffer, len(mesh.vertices), mesh.material, transparent))
            for key, pixels in mesh.material.textures.values():
                if key in self._textures: continue
                h, w = pixels.shape[:2]
                image = QImage(pixels.data, w, h, w*4, QImage.Format.Format_RGBA8888).copy()
                texture = QOpenGLTexture(image, QOpenGLTexture.MipMapGeneration.GenerateMipMaps)
                texture.setMinMagFilters(QOpenGLTexture.Filter.LinearMipMapLinear, QOpenGLTexture.Filter.Linear)
                texture.setWrapMode(QOpenGLTexture.WrapMode.Repeat)
                self._textures[key] = texture
        self._pending_upload = False
    def paintGL(self):
        if not hasattr(self, '_functions'): return
        f = self._functions
        f.glClearColor(.075, .082, .091, 1.)
        f.glClear(0x4000 | 0x0100)
        if self._program is None: return
        try:
            if self._pending_upload: self._upload()
            if self.scene is None: return
            f.glEnable(0x0B71)  # DEPTH_TEST
            f.glDisable(0x0B44)  # cull: inspect both sides, no missing thin glass
            radius = self.scene.radius
            projection = QMatrix4x4()
            projection.perspective(38., max(.1,self.width()/max(1.,self.height())), max(radius*.005,.0001), radius*100.)
            modelview = QMatrix4x4()
            modelview.translate(0.,0.,-radius*3.5*self._zoom)
            modelview.rotate(self._pitch,1.,0.,0.)
            modelview.rotate(self._yaw,0.,1.,0.)
            modelview.rotate(-90.,1.,0.,0.)  # Bethesda Z-up -> view Y-up
            center = self.scene.center
            modelview.translate(-float(center[0]),-float(center[1]),-float(center[2]))
            p = self._program; p.bind()
            def uniform(name, value):
                p.setUniformValue(p.uniformLocation(name), value)
            uniform('uMVP', projection * modelview)
            uniform('uMV', modelview)
            uniform('uMode', self.mode)
            uniform('uVertexColor', int(self.vertex_colors))
            for index, name in enumerate(('uBase','uNormal','uSpec','uGlow')): uniform(name,index)
            for buffer, count, material, transparent in sorted(self._gpu,key=lambda v:v[3]):
                fields = material.fields
                if transparent:
                    f.glEnable(0x0BE2); f.glBlendFunc(0x0302,0x0303); f.glDepthMask(False)
                else:
                    f.glDisable(0x0BE2); f.glDepthMask(True)
                present = []
                tile = int(fields.get('TileFlags',3))
                for index, role in enumerate(('base','normal','specular','glow')):
                    pair = material.textures.get(role)
                    present.append(1. if pair else 0.)
                    if pair:
                        texture = self._textures[pair[0]]
                        texture.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionS,QOpenGLTexture.WrapMode.Repeat if tile&1 else QOpenGLTexture.WrapMode.ClampToEdge)
                        texture.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionT,QOpenGLTexture.WrapMode.Repeat if tile&2 else QOpenGLTexture.WrapMode.ClampToEdge)
                        texture.bind(index)
                uniform('uMaps',QVector4D(*present))
                uniform('uUV',QVector4D(*[float(fields.get(name,default)) for name,default in (('UOffset',0),('VOffset',0),('UScale',1),('VScale',1))]))
                tint = fields.get('BaseColor',(1.,1.,1.))
                scale = float(fields.get('BaseColorScale',1.))
                uniform('uTint',QVector4D(*[float(x)*scale for x in tint],float(fields.get('Alpha',1.))))
                uniform('uSpecParams',QVector4D(float(fields.get('SpecularMult',1.)) if fields.get('SpecularEnabled',True) else 0.,float(fields.get('Smoothness',1.)),0.,0.))
                uniform('uSpecColor',QVector3D(*fields.get('SpecularColor',(1.,1.,1.))))
                uniform('uEmittance',QVector3D(*[float(x)*float(fields.get('EmittanceMult',1.)) for x in fields.get('EmittanceColor',(1.,1.,1.))]))
                uniform('uModelSpace',int(bool(fields.get('ModelSpaceNormals'))))
                uniform('uUseAlpha',int(transparent or bool(fields.get('AlphaTest'))))
                uniform('uAlphaTest',float(fields.get('AlphaTestRef',128))/255. if fields.get('AlphaTest') else 0.)
                buffer.bind()
                for name, offset, size in (('aPosition',0,3),('aNormal',12,3),('aUV',24,2),('aColor',32,4)):
                    location = p.attributeLocation(name)
                    p.enableAttributeArray(location)
                    p.setAttributeBuffer(location,0x1406,offset,size,48)
                f.glDrawArrays(0x0004,0,count)
                buffer.release()
            p.release()
            f.glDepthMask(True); f.glDisable(0x0BE2)
        except Exception as exc:
            if not self._error:
                self._error = str(exc); self.render_error.emit('Preview rendering failed: '+str(exc))
    def mousePressEvent(self,event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last = event.position(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
    def mouseMoveEvent(self,event):
        if self._last is not None:
            delta = event.position()-self._last; self._last=event.position()
            self._yaw += delta.x()*.4; self._pitch = max(-89.,min(89.,self._pitch+delta.y()*.4))
            self.update()
    def mouseReleaseEvent(self,event):
        self._last = None; self.setCursor(Qt.CursorShape.OpenHandCursor)
    def wheelEvent(self,event):
        self._zoom = max(.3,min(12.,self._zoom*math.exp(-event.angleDelta().y()/1200.)))
        self.update(); event.accept()
    def mouseDoubleClickEvent(self,event): self.fit()

class NifPreviewPanel(QWidget):
    busy_changed = Signal(bool)
    idle = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._path = ''
        self._scene = None
        self._shutting_down = False
        self._failed = False
        self._needs_reload = False
        self._roots_signature = ('', '')
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(10)
        top = QHBoxLayout()
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self._choose_nif)
        self.file_label = QLabel(); self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self.open_button); top.addWidget(self.file_label,1)
        self.reload_button = QPushButton(); self.reload_button.clicked.connect(lambda:self.open_nif(self._path))
        self.reload_button.setEnabled(False)
        _ghost_button(self.reload_button)
        top.addWidget(self.reload_button)
        self.cancel_button = QPushButton(); self.cancel_button.clicked.connect(self.cancel); self.cancel_button.hide()
        top.addWidget(self.cancel_button); layout.addLayout(top)
        self.converted_label,self.source_label = QLabel(),QLabel()
        self.converted_edit,self.source_edit = QLineEdit(),QLineEdit()
        self.converted_pick,self.source_pick = QPushButton('…'),QPushButton('…')
        for label,edit,pick in ((self.converted_label,self.converted_edit,self.converted_pick),(self.source_label,self.source_edit,self.source_pick)):
            row=QHBoxLayout(); label.setMinimumWidth(124); row.addWidget(label); row.addWidget(edit,1); row.addWidget(pick)
            _ghost_button(pick, compact=True)
            pick.clicked.connect(lambda _,e=edit:self._choose_root(e))
            edit.editingFinished.connect(self._resource_roots_edited)
            layout.addLayout(row)
        self.toolbar=QHBoxLayout()
        self.mode_combo=QComboBox(); self.mode_combo.setMinimumWidth(190); self.mode_combo.addItems(['','','']); self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.vertex_check=QCheckBox(); self.vertex_check.toggled.connect(self._colors_changed)
        self.fit_button=QPushButton(); self.fit_button.clicked.connect(lambda:self.viewport.fit())
        _ghost_button(self.fit_button)
        self.help_label=QLabel(); self.help_label.setStyleSheet('color:#8a8983;font-size:11px;')
        self.toolbar.addWidget(self.mode_combo); self.toolbar.addWidget(self.vertex_check); self.toolbar.addStretch(); self.toolbar.addWidget(self.help_label); self.toolbar.addWidget(self.fit_button)
        layout.addLayout(self.toolbar)
        self.viewport=NifViewport(); self.viewport.render_error.connect(self._render_error)
        layout.addWidget(self.viewport,1)
        self.status_label=QLabel(); self.status_label.setWordWrap(True); layout.addWidget(self.status_label)
        self.disclaimer=QLabel(); self.disclaimer.setWordWrap(True); self.disclaimer.setStyleSheet('color:#8a8983;font-size:11px;'); layout.addWidget(self.disclaimer)
        self.details_button=QPushButton(); self.details_button.setCheckable(True); self.details_button.setChecked(False)
        _ghost_button(self.details_button)
        self.details_button.toggled.connect(self._toggle_details); layout.addWidget(self.details_button)
        self.details=QPlainTextEdit(); self.details.setReadOnly(True); self.details.setMaximumHeight(110); self.details.hide(); layout.addWidget(self.details)
        self.retranslate()
    def retranslate(self):
        self.open_button.setText(_text('选择 NIF','Open NIF'))
        self.open_button.setToolTip(_text('从电脑读取 NIF 模型，所有文件仅在本地处理。','Read a local NIF model. Files stay on your computer.'))
        self.reload_button.setText(_text('重新加载','Reload')); self.reload_button.setToolTip(_text('按当前资源目录重新读取模型与材质。','Reload geometry and materials from the selected roots.'))
        self.cancel_button.setText(_text('取消加载','Cancel'))
        self.converted_label.setText(_text('转换结果目录','Converted assets'))
        self.source_label.setText(_text('原资源目录（备用）','Source fallback'))
        self.converted_edit.setPlaceholderText(_text('选择含 materials / textures 的转换结果目录','Folder containing converted materials / textures'))
        self.source_edit.setPlaceholderText(_text('缺失资源时使用；不会修改原文件','Used only for missing assets; source files remain unchanged'))
        for widget in (self.converted_edit,self.converted_pick):widget.setToolTip(_text('优先读取此目录。可选择一次转换的运行目录或 converted 文件夹。','Priority root. Choose a conversion run directory or its converted folder.'))
        for widget in (self.source_edit,self.source_pick):widget.setToolTip(_text('转换结果缺少引用时在此查找；诊断中标明备用来源。','Fallback for missing references; diagnostics identify fallback assets.'))
        for i,(zh,en) in enumerate((('材质光照','Material lighting'),('基础颜色','Base color'),('表面法线','Surface normals'))):self.mode_combo.setItemText(i,_text(zh,en))
        self.mode_combo.setToolTip(_text('切换材质光照、基础颜色或法线检查。','Inspect lighting, base color, or surface normals.'))
        self.vertex_check.setText(_text('顶点色','Vertex colors')); self.vertex_check.setToolTip(_text('将 NIF 顶点色乘入材质颜色；默认关闭以便检查贴图。','Multiply NIF vertex colors into the material; off by default for texture inspection.'))
        self.fit_button.setText(_text('适应视图','Fit')); self.fit_button.setToolTip(_text('重置视角和缩放，使模型完整显示。','Reset the camera and fit the entire model.'))
        self.help_label.setText(_text('拖动旋转 · 滚轮缩放 · 双击复位','Drag to orbit · Wheel to zoom · Double-click to fit'))
        self.disclaimer.setText(_text('本地只读预览 · 骨骼为绑定姿态 · 工作室光照近似展示，不等同于游戏渲染。','Local read-only preview · Bind pose for skins · Approximate studio lighting, not the game renderer.'))
        self.details_button.setText(_text('资源诊断与缺失引用','Resource diagnostics / missing references'))
        self.details_button.setToolTip(_text('查看实际使用的材质、贴图、备用来源和未支持的效果。','Show loaded materials, textures, fallback origins, and unsupported effects.'))
        if not self._path:self.file_label.setText(_text('支持拖入本地 .nif','Drop a local .nif here'))
        self._show_summary()
    def _current_roots_signature(self):
        values=[]
        for edit in (self.converted_edit,self.source_edit):
            text=edit.text().strip()
            if not text:
                values.append('')
                continue
            try: values.append(str(Path(text).resolve()).replace('\\','/').casefold())
            except (OSError,ValueError): values.append(text.replace('\\','/').rstrip('/').casefold())
        return tuple(values)
    def _resource_roots_edited(self):
        if self.is_busy(): return False
        signature=self._current_roots_signature()
        if signature == self._roots_signature: return True
        self._roots_signature=signature
        if self._scene is not None:
            self._scene=None
            self.viewport.set_scene(None)
            self.details.clear()
            self._needs_reload=True
        self._show_summary()
        return True
    def set_resource_roots(self,converted_root='',source_root=''):
        if self.is_busy(): return False
        self.converted_edit.setText(str(converted_root or '')); self.source_edit.setText(str(source_root or ''))
        return self._resource_roots_edited()
    def is_busy(self): return self._worker is not None
    def cancel(self):
        if self._worker is not None:
            self._worker.cancel_event.set(); self.cancel_button.setEnabled(False)
            self.status_label.setText(_text('正在取消加载…','Cancelling load…'))
    def shutdown(self):
        self._shutting_down = True
        if self.is_busy(): self.cancel(); return False
        self.viewport.release_gl(); return True
    def _choose_nif(self):
        path,_=QFileDialog.getOpenFileName(self,_text('选择模型','Open model'),self._path,'NIF (*.nif)')
        if path:self.open_nif(path)
    def _choose_root(self,edit):
        path=QFileDialog.getExistingDirectory(self,_text('选择资源目录','Choose resource directory'),edit.text())
        if path:
            edit.setText(path)
            self._resource_roots_edited()
    def open_nif(self,path):
        if not path or self.is_busy() or self._shutting_down:return False
        path=Path(path)
        if path.suffix.lower()!='.nif':return False
        self._path=str(path)
        if not self.source_edit.text().strip():
            for ancestor in path.parents:
                if ancestor.name.casefold()=='meshes':self.source_edit.setText(str(ancestor.parent));break
        self.file_label.setText(path.name);self.file_label.setToolTip(str(path))
        self._failed=False
        self._needs_reload=False
        self._roots_signature=self._current_roots_signature()
        self._scene=None
        self.viewport.set_scene(None)
        self.details.clear()
        self.status_label.setText(_text('正在后台读取模型与材质…','Loading geometry and materials in the background…'))
        self._worker=LoadWorker(str(path),self.converted_edit.text().strip(),self.source_edit.text().strip(),self)
        self._worker.loaded.connect(self._loaded);self._worker.failed.connect(self._load_failed);self._worker.finished.connect(self._finished)
        for widget in (self.open_button,self.reload_button,self.converted_edit,self.source_edit,self.converted_pick,self.source_pick):widget.setEnabled(False)
        self.cancel_button.setEnabled(True);self.cancel_button.show();self.busy_changed.emit(True);self._worker.start()
        return True
    def _loaded(self,scene):
        if self._shutting_down:return
        self._scene=scene;self.viewport.set_scene(scene)
        self.details.setPlainText('\n'.join(scene.diagnostics));self._show_summary()
    def _load_failed(self,message):
        self._failed=True
        self.status_label.setText(_text('读取失败：','Load failed: ')+message)
        self.details.setPlainText(message)
    def _finished(self):
        worker=self._worker
        cancelled=worker.cancel_event.is_set() if worker else False
        self._worker=None
        if worker:worker.deleteLater()
        self.cancel_button.hide()
        for widget in (self.open_button,self.reload_button,self.converted_edit,self.source_edit,self.converted_pick,self.source_pick):widget.setEnabled(True)
        if cancelled:self.status_label.setText(_text('已取消加载。','Load cancelled.'))
        self.busy_changed.emit(False);self.idle.emit()
    def _show_summary(self):
        if self._needs_reload and not self.is_busy():
            self.status_label.setText(_text('资源目录已改变，旧预览已清除。请点击“重新加载”查看新材质。','Resource roots changed. The previous preview was cleared; click Reload to view the new materials.'))
        elif self._scene:
            scene=self._scene
            converted=sum(m.material.source=='converted' for m in scene.meshes)
            self.status_label.setText(_text(f'{len(scene.meshes):,} 个网格 · {scene.triangles:,} 个三角面 · {converted} 个网格使用转换材质',f'{len(scene.meshes):,} meshes · {scene.triangles:,} triangles · {converted} meshes using converted materials'))
        elif not self.is_busy():self.status_label.setText(_text('选择 NIF 和转换结果目录，即可检查模型上的材质。','Open a NIF and choose converted assets to inspect materials on the model.'))
    def _render_error(self,message):
        self.status_label.setText(message);self.details.appendPlainText(message)
    def _toggle_details(self,visible):self.details.setVisible(visible)
    def _mode_changed(self,mode):
        if hasattr(self,'viewport'):self.viewport.set_mode(mode)
    def _colors_changed(self,enabled):self.viewport.set_vertex_colors(enabled)
    def dragEnterEvent(self,event):
        if not self.is_busy() and any(u.isLocalFile() and u.toLocalFile().lower().endswith('.nif') for u in event.mimeData().urls()):event.acceptProposedAction()
    def dropEvent(self,event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith('.nif'):
                self.open_nif(url.toLocalFile());event.acceptProposedAction();break
