from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from PySide6.QtCore import QTimer, Qt, QLibraryInfo, QCoreApplication
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import (
        QApplication, QWidget, QMainWindow, QLabel, QTableWidget, QTableWidgetItem,
        QHBoxLayout, QVBoxLayout, QPushButton, QTabWidget, QHeaderView,
        QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QComboBox
    )
except Exception as e:  # pragma: no cover
    raise SystemExit("PySide6 not installed. Run: pip install PySide6")

import requests


def _env_port() -> int:
    try:
        return int(os.environ.get("PORT", "5055"))
    except Exception:
        return 5055


@dataclass
class WeaponRow:
    name: str
    ammo: int
    in_range: bool
    armed: str
    cooldown_s: int


class MainWindow(QMainWindow):
    def __init__(self, base: str) -> None:
        super().__init__()
        self.base = base.rstrip('/')
        self.setWindowTitle("Falkland V2 — Desktop")
        self.setMinimumSize(800, 480)
        self.resize(800, 600)
        # Touch-friendly scaling
        self._touch_scale = 1.0
        try:
            self._touch_scale = float(os.environ.get('TOUCH_SCALE', os.environ.get('TOUCH', '1'))) if os.environ.get('TOUCH') else float(os.environ.get('TOUCH_SCALE', '1.0'))
        except Exception:
            self._touch_scale = 1.0

        # HUD
        self.hud = QLabel("—")
        self.hud.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        top = QVBoxLayout()
        top.addWidget(self.hud)
        # Removed global controls; buttons now live in station tabs

        # Tabs
        self.tabs = QTabWidget()
        # Radar tab (contacts)
        rdr_row = QHBoxLayout()
        self.rdr_scan_btn = QPushButton("Scan")
        self.rdr_scan_btn.setMinimumHeight(int(34 * self._touch_scale))
        self.rdr_scan_btn.clicked.connect(self.scan_now)
        rdr_row.addWidget(self.rdr_scan_btn)
        rdr_row.addStretch(1)
        self.rdr_alert = QLabel("")
        self.rdr_alert.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.tbl_contacts = QTableWidget(0, 5)
        self.tbl_contacts.setHorizontalHeaderLabels(["ID", "Type", "Cell", "rng(nm)", "spd/hdg"])
        self.tbl_contacts.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_contacts.verticalHeader().setVisible(False)
        radar_box = QVBoxLayout(); radar_box.addLayout(rdr_row); radar_box.addWidget(self.rdr_alert); radar_box.addWidget(self.tbl_contacts)
        radar_w = QWidget(); radar_w.setLayout(radar_box)

        # Weapons tab + Lock panel
        self.lock_label = QLabel('Primary: —')
        self.lock_select = QComboBox()
        self.lock_btn = QPushButton('Lock')
        self.lock_nearest_btn = QPushButton('Lock Nearest')
        self.lock_picker_btn = QPushButton('Pick…')
        self.unlock_btn = QPushButton('Unlock')
        self.lock_btn.clicked.connect(self._lock_selected)
        self.lock_nearest_btn.clicked.connect(self.lock_nearest)
        self.lock_picker_btn.clicked.connect(self.lock_picker)
        self.unlock_btn.clicked.connect(self.unlock)
        lock_row = QHBoxLayout(); lock_row.addWidget(self.lock_label); lock_row.addWidget(self.lock_select); lock_row.addWidget(self.lock_btn); lock_row.addWidget(self.lock_nearest_btn); lock_row.addWidget(self.lock_picker_btn); lock_row.addWidget(self.unlock_btn); lock_row.addStretch(1)
        self.tbl_weapons = QTableWidget(0, 7)
        self.tbl_weapons.setHorizontalHeaderLabels(["Weapon", "Ammo", "Min–Max (nm)", "In Range?", "State", "Test", "Fire"])
        self.tbl_weapons.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_weapons.verticalHeader().setVisible(False)
        weapons_box = QVBoxLayout(); weapons_box.addLayout(lock_row); weapons_box.addWidget(self.tbl_weapons)
        weapons_w = QWidget(); weapons_w.setLayout(weapons_box)

        # NAV tab
        self.tbl_nav_fleet = QTableWidget(0, 4)
        self.tbl_nav_fleet.setHorizontalHeaderLabels(["Fleet status", "Grid", "Speed", "Course"]) 
        self.tbl_nav_fleet.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_nav_fleet.verticalHeader().setVisible(False)
        self.nav_hdg = QLabel("hdg —°"); self.nav_spd = QLabel("spd — kn");
        self.nav_rudder = QLabel(""); self.nav_edge = QLabel("")
        self.nav_btn_close = QPushButton("Hermes: Close In"); self.nav_btn_stand = QPushButton("Hermes: Stand Off")
        for b in (self.nav_btn_close, self.nav_btn_stand):
            b.setMinimumHeight(int(34 * self._touch_scale))
        self.nav_btn_close.clicked.connect(lambda: self._get('/nav/hermes/close_in'))
        self.nav_btn_stand.clicked.connect(lambda: self._get('/nav/hermes/stand_off'))
        self.nav_apply_course = QPushButton("Course →"); self.nav_apply_speed = QPushButton("Speed →")
        from PySide6.QtWidgets import QLineEdit
        self.nav_in_hdg = QLineEdit(); self.nav_in_hdg.setPlaceholderText('degrees')
        self.nav_in_spd = QLineEdit(); self.nav_in_spd.setPlaceholderText('knots')
        self.nav_apply_course.clicked.connect(self._nav_apply_course)
        self.nav_apply_speed.clicked.connect(self._nav_apply_speed)
        nav_box = QVBoxLayout()
        # Fleet table first
        nav_box.addWidget(self.tbl_nav_fleet)
        row1 = QHBoxLayout(); row1.addWidget(QLabel('Course')); row1.addWidget(self.nav_in_hdg); row1.addWidget(self.nav_apply_course); row1.addStretch(1)
        row2 = QHBoxLayout(); row2.addWidget(QLabel('Speed')); row2.addWidget(self.nav_in_spd); row2.addWidget(self.nav_apply_speed); row2.addStretch(1)
        row3 = QHBoxLayout(); row3.addWidget(self.nav_hdg); row3.addWidget(self.nav_spd); row3.addWidget(self.nav_rudder); row3.addWidget(self.nav_edge); row3.addStretch(1)
        row4 = QHBoxLayout(); row4.addWidget(self.nav_btn_close); row4.addWidget(self.nav_btn_stand); row4.addStretch(1)
        for r in (row1,row2,row3,row4): nav_box.addLayout(r)
        nav_w = QWidget(); nav_w.setLayout(nav_box)

        # COMMS/RADIO tab (CAP controls minimal)
        from PySide6.QtWidgets import QLineEdit
        self.cap_ready = QLabel('CAP: —')
        self.hermes_hdr = QLabel('Hermes: —')
        self.cap_btn_intercept = QPushButton('Launch Intercept')
        self.cap_btn_intercept.clicked.connect(lambda: self._post('/cap/request', {}))
        self.cap_cell = QLineEdit(); self.cap_cell.setPlaceholderText('Cell (e.g., K13)')
        self.cap_btn_to_cell = QPushButton('CAP to Cell')
        self.cap_btn_to_cell.clicked.connect(self._cap_to_cell)
        self.cap_tasks = QTableWidget(0, 7)
        self.cap_tasks.setHorizontalHeaderLabels(["N", "Cur", "Target", "rng(nm)", "TOT(s)", "TOS(s)", "Status"])
        self.cap_tasks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cap_tasks.verticalHeader().setVisible(False)
        self.radio_in = QLineEdit(); self.radio_in.setPlaceholderText('Transmit…')
        self.radio_btn = QPushButton('Send')
        for b in (self.cap_btn_intercept, self.cap_btn_to_cell, self.radio_btn):
            b.setMinimumHeight(int(34 * self._touch_scale))
        self.radio_btn.clicked.connect(self._radio_send)
        comms_box = QVBoxLayout()
        r1 = QHBoxLayout(); r1.addWidget(self.cap_ready); r1.addWidget(self.hermes_hdr); r1.addStretch(1)
        r2 = QHBoxLayout(); r2.addWidget(self.cap_btn_intercept); r2.addStretch(1)
        r3 = QHBoxLayout(); r3.addWidget(self.cap_cell); r3.addWidget(self.cap_btn_to_cell); r3.addStretch(1)
        # ROE prompt row
        self.cap_perm_label = QLabel("")
        self.cap_btn_engage = QPushButton('Engage')
        self.cap_btn_hold = QPushButton('Hold')
        self.cap_btn_engage.clicked.connect(lambda _=False: self._cap_authorize(True))
        self.cap_btn_hold.clicked.connect(lambda _=False: self._cap_authorize(False))
        rperm = QHBoxLayout(); rperm.addWidget(self.cap_perm_label); rperm.addWidget(self.cap_btn_engage); rperm.addWidget(self.cap_btn_hold); rperm.addStretch(1)
        r4 = QHBoxLayout(); r4.addWidget(self.radio_in); r4.addWidget(self.radio_btn)
        for r in (r1,r2,r3): comms_box.addLayout(r)
        comms_box.addLayout(rperm)
        comms_box.addWidget(self.cap_tasks)
        comms_box.addLayout(r4)
        comms_w = QWidget(); comms_w.setLayout(comms_box)

        # ENG tab (systems + health)
        self.eng_health = QLabel('Ship: —%  Hermes: —%   Teams: —/—')
        self.tbl_eng = QTableWidget(0, 5)
        self.tbl_eng.setHorizontalHeaderLabels(["System", "Status", "Timer(s)", "Team", "Action"])
        self.tbl_eng.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_eng.verticalHeader().setVisible(False)
        eng_box = QVBoxLayout(); eng_box.addWidget(self.eng_health); eng_box.addWidget(self.tbl_eng); eng_box.addStretch(1)
        eng_w = QWidget(); eng_w.setLayout(eng_box)

        # Add tabs in desired order: NAV, RDR, WPN/FCR, COMMS, ENG, MENU (rightmost)
        self.tabs.addTab(nav_w, "NAV")
        self.tabs.addTab(radar_w, "RDR")
        self.tabs.addTab(weapons_w, "WPN/FCR")
        self.tabs.addTab(comms_w, "COMMS")
        self.tabs.addTab(eng_w, "ENG")

        # MENU tab (rightmost)
        menu_box = QVBoxLayout()
        from PySide6.QtWidgets import QPushButton
        self.menu_selftest = QPushButton('Self Test')
        self.menu_reset = QPushButton('Reset Runtime')
        self.menu_selftest.clicked.connect(lambda _=False: self._get('/diag/selftest'))
        self.menu_reset.clicked.connect(lambda _=False: self._post('/diag/reset', {}))
        menu_box.addWidget(self.menu_selftest)
        menu_box.addWidget(self.menu_reset)
        menu_box.addStretch(1)
        menu_w = QWidget(); menu_w.setLayout(menu_box)
        self.tabs.addTab(menu_w, "MENU")

        top.addWidget(self.tabs)
        root = QWidget(); root.setLayout(top)
        self.setCentralWidget(root)

        # poll timer
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        # first refresh
        QTimer.singleShot(200, self.refresh)

        # --- simple audio (pygame mixer) ---
        self._audio_dir = str((Path(__file__).resolve().parent / 'data' / 'sounds'))
        self._audio_ready = False
        self._last_stamps: Dict[str, Any] = {}
        self._eng_teams_txt = '—/—'
        try:
            import pygame  # type: ignore
            pygame.mixer.init()
            self._pg = pygame
            self._audio_ready = True
        except Exception:
            self._pg = None

    # --- networking helpers
    def _get(self, path: str) -> Dict[str, Any]:
        try:
            r = requests.get(self.base + path, timeout=2)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _post(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            r = requests.post(self.base + path, json=(json_body or {}), timeout=2)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- actions
    def scan_now(self):
        self._get("/api/command?cmd=%2Fradar%20scan")

    def lock_nearest(self):
        # Prefer locking by current top_threat_id to avoid backend 'nearest' ambiguities
        j = self._get('/api/status')
        tid = None
        try:
            tid = (j.get('top_threat_id') if isinstance(j, dict) else None)
        except Exception:
            tid = None
        if tid:
            self._get(f"/api/command?cmd=%2Fradar%20lock%20{tid}")
        else:
            self._get("/api/command?cmd=%2Fradar%20lock%20nearest")

    def lock_picker(self):
        # Build a simple selection dialog from current contacts
        j = self._get("/api/status")
        items: List[Dict[str, Any]] = j.get('contacts') or [] if isinstance(j, dict) else []
        if not items:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Lock Target")
        v = QVBoxLayout(dlg)
        lw = QListWidget(dlg)
        formatted: List[tuple[int, str]] = []
        for it in items:
            try:
                cid = int(it.get('id'))
            except Exception:
                continue
            typ = str(it.get('type') or it.get('class') or '')
            name = str(it.get('name') or '')
            rng = it.get('range_nm'); cell = str(it.get('cell') or it.get('grid') or '')
            label = f"{cid:>2} — {typ} {name}   rng {rng} nm  {cell}"
            formatted.append((cid, label))
        for _cid, label in formatted:
            lw.addItem(QListWidgetItem(label))
        v.addWidget(lw)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dlg)
        v.addWidget(buttons)
        chosen: Optional[int] = None
        def on_accept():
            nonlocal chosen
            row = lw.currentRow()
            if row >= 0:
                chosen = formatted[row][0]
            dlg.accept()
        def on_reject():
            dlg.reject()
        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(on_reject)
        dlg.exec()
        if chosen is not None:
            self._get(f"/api/command?cmd=%2Fradar%20lock%20{chosen}")

    def unlock(self):
        self._get("/api/command?cmd=%2Fradar%20unlock")

    def _arm_toggle(self, name: str, target: str):
        self._post("/weapons/arm", {"name": name, "state": target})

    def _fire(self, name: str, mode: str):
        self._post("/weapons/fire", {"name": name, "mode": mode})

    # --- UI update
    def refresh(self):
        j = self._get("/api/status")
        if not j or j.get("ok") is False:
            self.hud.setText("Server unavailable")
            return
        hud = j.get("hud") or ""; self.hud.setText(hud)
        self._render_rdr_alerts(j)
        self._render_contacts(j.get("contacts") or [])
        self._render_weapons(j.get("weapons") or [])
        self._render_lock_panel(j)
        # NAV snapshot (coarse)
        st = j.get('state') or {}
        ship = st.get('ship') or {}
        cell = None
        try:
            # Prefer ownfleet cell if available
            of = j.get('ownfleet') or []
            own = next((u for u in of if str(u.get('id',''))=='own'), None)
            if own:
                cell = own.get('cell')
        except Exception:
            cell = None
        hdg_val = ship.get('heading','—'); spd_val = ship.get('speed','—')
        self.nav_hdg.setText(f"hdg {hdg_val}°" + (f"  cell {cell}" if cell else ""))
        self.nav_spd.setText(f"spd {spd_val} kn")
        # Fleet table
        self._render_nav_fleet(j)
        # Rudder-time countdown (1s per deg to turn_target)
        try:
            tgt = (j.get('nav') or {}).get('turn_target')
            if isinstance(tgt, (int,float)) and isinstance(hdg_val, (int,float)):
                dd = abs(((float(tgt) - float(hdg_val) + 180) % 360) - 180)
                self.nav_rudder.setText(f"rudder {int(round(dd))}s")
            else:
                self.nav_rudder.setText("")
        except Exception:
            self.nav_rudder.setText("")
        # Board-edge predictor (<1h warning)
        try:
            board_n = int((j.get('grid') or {}).get('board_n') or 26)
            cnm = float(st.get('CELL_NM', 1.0)) if isinstance(st, dict) else 1.0
            col = int(ship.get('col')) if ship.get('col') is not None else None
            row = int(ship.get('row')) if ship.get('row') is not None else None
            spd = float(spd_val) if isinstance(spd_val, (int,float)) else None
            hdg = float(hdg_val) if isinstance(hdg_val, (int,float)) else None
            if None not in (col,row,spd,hdg) and cnm > 0:
                import math
                vx = math.sin(math.radians(hdg)) * (spd / cnm)  # cells/hour in x
                vy = -math.cos(math.radians(hdg)) * (spd / cnm) # cells/hour in y
                times = []
                if vx > 0: times.append((board_n - col) / vx)
                if vx < 0: times.append((1 - col) / vx)  # vx<0 makes positive time
                if vy > 0: times.append((board_n - row) / vy)
                if vy < 0: times.append((1 - row) / vy)
                times = [t for t in times if t is not None and t >= 0]
                if times:
                    tmin_h = min(times)
                    tmin_m = int(round(tmin_h * 60))
                    if tmin_m <= 60:
                        self.nav_edge.setText(f'edge {tmin_m}m')
                        self.nav_edge.setStyleSheet('color: #e6b800; font-weight: bold;')
                    else:
                        self.nav_edge.setText('')
                        self.nav_edge.setStyleSheet('')
                else:
                    self.nav_edge.setText('')
                    self.nav_edge.setStyleSheet('')
            else:
                self.nav_edge.setText('')
                self.nav_edge.setStyleSheet('')
        except Exception:
            self.nav_edge.setText('')
            self.nav_edge.setStyleSheet('')
        # CAP
        cap = j.get('cap') or {}
        if cap:
            self.cap_ready.setText(f"CAP READY: {'YES' if cap.get('ready') else 'NO'}  pairs {cap.get('pairs',0)}  cooldown {cap.get('cooldown_s',0)}s  committed {cap.get('committed',0)}")
            self._render_cap_tasks(cap.get('tasks') or [])
        # enable intercept only when we have a top threat id
        try:
            self.cap_btn_intercept.setEnabled(bool(j.get('top_threat_id')))
        except Exception:
            pass
        # Hermes header from ownfleet
        try:
            of = j.get('ownfleet') or []
            herm = next((u for u in of if str(u.get('id',''))=='hermes' or str(u.get('name','')).lower().find('hermes')>=0), None)
            if herm:
                self.hermes_hdr.setText(f"Hermes: {herm.get('cell','—')} hdg {herm.get('heading','—')} spd {herm.get('speed','—')}")
            else:
                self.hermes_hdr.setText('Hermes: —')
        except Exception:
            self.hermes_hdr.setText('Hermes: —')
        # ROE ask poll
        self._cap_update_roe()
        # ENG health
        lives = st.get('lives'); maxl = st.get('max_lives')
        herm_l = st.get('hermes_lives'); herm_ml = st.get('hermes_max_lives')
        def pct(a,b):
            try:
                return int(round(100*int(a)/int(b))) if a is not None and b else 100
            except Exception:
                return 100
        # ENG systems fetch
        self._eng_refresh()
        teams_txt = getattr(self, '_eng_teams_txt', '—/—')
        self.eng_health.setText(f"Ship: {pct(lives,maxl)}%  Hermes: {pct(herm_l,herm_ml)}%   Teams: {teams_txt}")
        # Audio cues (edge-triggered)
        self._audio_update(j.get('audio') or {})

    def _render_contacts(self, items: List[Dict[str, Any]]):
        self.tbl_contacts.setRowCount(0)
        for it in items[:10]:
            row = self.tbl_contacts.rowCount(); self.tbl_contacts.insertRow(row)
            vals = [
                str(it.get("id", "")),
                str(it.get("type") or it.get("class") or ""),
                str(it.get("cell") or it.get("grid") or ""),
                f"{it.get('range_nm', '—')}",
                f"{it.get('SPD','')}/{it.get('CRS','')}"
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsEnabled)
                self.tbl_contacts.setItem(row, col, item)

    def _render_weapons(self, items: List[Dict[str, Any]]):
        self.tbl_weapons.setRowCount(0)
        for w in items:
            row = self.tbl_weapons.rowCount(); self.tbl_weapons.insertRow(row)
            name = str(w.get('name',''))
            ammo = int(w.get('ammo', 0) or 0)
            inr = bool(w.get('in_range'))
            armed = str(w.get('armed', 'Safe'))
            cd = int(w.get('cooldown_s', 0) or 0)
            rmin = w.get('min_nm'); rmax = w.get('max_nm')
            minmax = '—'
            try:
                if isinstance(rmin, (int,float)) or isinstance(rmax, (int,float)):
                    a = (f"{float(rmin):.0f}" if isinstance(rmin,(int,float)) else '')
                    b = (f"{float(rmax):.0f}" if isinstance(rmax,(int,float)) else '')
                    dash = '–' if a and b else ''
                    minmax = f"{a}{dash}{b}"
            except Exception:
                minmax = '—'

            # Name
            item = QTableWidgetItem(name); item.setFlags(Qt.ItemIsEnabled)
            self.tbl_weapons.setItem(row, 0, item)
            # Ammo
            item2 = QTableWidgetItem(str(ammo)); item2.setFlags(Qt.ItemIsEnabled)
            self.tbl_weapons.setItem(row, 1, item2)
            # Min–Max
            item3a = QTableWidgetItem(minmax); item3a.setFlags(Qt.ItemIsEnabled)
            self.tbl_weapons.setItem(row, 2, item3a)
            # In range
            item3 = QTableWidgetItem("YES" if inr else "NO"); item3.setFlags(Qt.ItemIsEnabled)
            self.tbl_weapons.setItem(row, 3, item3)
            # State + arm toggle
            btn_arm = QPushButton("Safe" if armed == 'Armed' else "Arm")
            target_state = 'Safe' if armed == 'Armed' else 'Armed'
            btn_arm.clicked.connect(lambda _=False, nm=name, st=target_state: self._arm_toggle(nm, st))
            self.tbl_weapons.setCellWidget(row, 4, btn_arm)
            # Test + Fire
            btn_test = QPushButton("Test")
            btn_fire = QPushButton("Fire")
            btn_test.setEnabled(armed == 'Armed' and ammo > 0 and cd <= 0)
            btn_fire.setEnabled(armed == 'Armed' and ammo > 0 and cd <= 0 and inr)
            btn_test.clicked.connect(lambda _=False, nm=name: self._fire(nm, 'test'))
            btn_fire.clicked.connect(lambda _=False, nm=name: self._fire(nm, 'real'))
            self.tbl_weapons.setCellWidget(row, 5, btn_test)
            self.tbl_weapons.setCellWidget(row, 6, btn_fire)

    def _render_lock_panel(self, j: Dict[str, Any]):
        # Populate select with contacts and show current primary
        items = j.get('contacts') or []
        self.lock_select.clear()
        for it in items:
            cid = str(it.get('id',''))
            typ = str(it.get('type') or it.get('class') or '')
            name = str(it.get('name') or '')
            rng = it.get('range_nm')
            cell = str(it.get('cell') or it.get('grid') or '')
            label = f"{cid} — {typ} {name}  {rng} nm  {cell}"
            self.lock_select.addItem(label, cid)
        locked = None
        try:
            locked = (j.get('radar') or {}).get('locked_id')
        except Exception:
            locked = None
        if locked is not None:
            # find item index whose data equals locked
            for i in range(self.lock_select.count()):
                if self.lock_select.itemData(i) and int(self.lock_select.itemData(i)) == int(locked):
                    self.lock_select.setCurrentIndex(i)
                    break
        # primary label
        if locked is not None:
            self.lock_label.setText(f'Primary: {locked}')
            self.unlock_btn.setEnabled(True)
        else:
            self.lock_label.setText('Primary: —')
            self.unlock_btn.setEnabled(False)

    def _lock_selected(self):
        idx = self.lock_select.currentIndex()
        if idx < 0:
            return
        cid = self.lock_select.itemData(idx)
        if not cid:
            return
        res = self._get(f"/api/command?cmd=%2Fradar%20lock%20{cid}")
        # If backend requires unlock-first (409), nudge UI
        if not res or not res.get('ok'):
            self.lock_label.setText('Primary: unlock first')

    def _render_rdr_alerts(self, j: Dict[str, Any]):
        try:
            tid = j.get('top_threat_id')
            if not tid:
                # include scan countdown if present
                left = None
                try:
                    left = (j.get('radar') or {}).get('scan_left_s')
                except Exception:
                    left = None
                if isinstance(left, int):
                    self.rdr_alert.setText(f'No priority threat — scan in {left}s')
                else:
                    self.rdr_alert.setText('No priority threat')
                self.rdr_alert.setStyleSheet('color: #99a;')
                return
            rng = None
            for it in (j.get('contacts') or []):
                try:
                    if int(it.get('id')) == int(tid):
                        rng = float(it.get('range_nm'))
                        break
                except Exception:
                    continue
            if rng is None:
                left = (j.get('radar') or {}).get('scan_left_s') if isinstance(j.get('radar'), dict) else None
                suffix = (f" — scan in {left}s" if isinstance(left, int) else '')
                self.rdr_alert.setText(f"PRIMARY {tid} — range —{suffix}")
                self.rdr_alert.setStyleSheet('color: #cc0;')
                return
            left = (j.get('radar') or {}).get('scan_left_s') if isinstance(j.get('radar'), dict) else None
            suffix = (f" — scan in {left}s" if isinstance(left, int) else '')
            if rng < 1.0:
                self.rdr_alert.setText(f"PRIMARY {tid} — {rng:.1f} nm — RED ALERT{suffix}")
                self.rdr_alert.setStyleSheet('color: #f33; font-weight: bold;')
            elif rng < 3.0:
                self.rdr_alert.setText(f"PRIMARY {tid} — {rng:.1f} nm — ALERT{suffix}")
                self.rdr_alert.setStyleSheet('color: #e6b800; font-weight: bold;')
            else:
                self.rdr_alert.setText(f"PRIMARY {tid} — {rng:.1f} nm{suffix}")
                self.rdr_alert.setStyleSheet('color: #9c9;')
        except Exception:
            self.rdr_alert.setText('—')
            self.rdr_alert.setStyleSheet('')

    def _render_cap_tasks(self, items: List[Dict[str, Any]]):
        try:
            self.cap_tasks.setRowCount(0)
        except Exception:
            return
        for t in items:
            row = self.cap_tasks.rowCount(); self.cap_tasks.insertRow(row)
            vals = [
                str(t.get('n','')),
                str(t.get('cur_cell','')),
                str(t.get('target_cell','')),
                (f"{t.get('range_nm'):.1f}" if isinstance(t.get('range_nm'), (int,float)) else '—'),
                (str(int(t.get('tot_s'))) if isinstance(t.get('tot_s'), (int,float)) else '—'),
                (str(int(t.get('tos_s'))) if isinstance(t.get('tos_s'), (int,float)) else '—'),
                str(t.get('status','')) + (" (VECTOR)" if t.get('vector') else '')
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setFlags(Qt.ItemIsEnabled)
                self.cap_tasks.setItem(row, col, item)

    # --- CAP ROE helpers ---
    def _cap_update_roe(self):
        j = self._get('/cap/roe')
        mid = None
        if j and j.get('ok') and isinstance(j.get('missions'), dict):
            for k, v in j['missions'].items():
                try:
                    if v.get('asked') and not v.get('authorized'):
                        mid = int(k)
                        break
                except Exception:
                    continue
        if mid is not None:
            self._cap_pending_id = mid
            self.cap_perm_label.setText(f'Authorize CAP Mission {mid} to ENGAGE?')
            for b in (self.cap_btn_engage, self.cap_btn_hold):
                b.setEnabled(True)
                b.setVisible(True)
            self.cap_perm_label.setVisible(True)
        else:
            self._cap_pending_id = None
            self.cap_perm_label.setText('')
            for b in (self.cap_btn_engage, self.cap_btn_hold):
                b.setVisible(False)
            self.cap_perm_label.setVisible(False)

    def _cap_authorize(self, yes: bool):
        mid = getattr(self, '_cap_pending_id', None)
        if mid is None:
            return
        self._post('/cap/authorize', {'id': int(mid), 'authorize': bool(yes)})
        # hide prompt and refresh
        self._cap_pending_id = None
        self._cap_update_roe()

    # --- audio helpers ---
    def _audio_update(self, audio: Dict[str, Any]):
        if not self._audio_ready:
            return
        try:
            last = audio.get('last_launch')
            if last and (self._last_stamps.get('launch_ts') != last.get('ts')):
                self._last_stamps['launch_ts'] = last.get('ts')
                key = str(last.get('weapon') or 'weapon_launch')
                file = self._sound_map().get(key, 'missile_launch.wav')
                self._play_sound(file)
        except Exception:
            pass
        try:
            res = audio.get('last_result')
            if res and (self._last_stamps.get('result_ts') != res.get('ts')):
                self._last_stamps['result_ts'] = res.get('ts')
                evt = str(res.get('event') or '')
                if evt == 'hit':
                    self._play_sound('hit.wav')
                elif evt == 'miss':
                    # Own ordnance misses are radio-only; no SFX
                    pass
        except Exception:
            pass
        try:
            alarm = audio.get('alarm')
            if alarm and (self._last_stamps.get('alarm_ts') != alarm.get('ts')):
                self._last_stamps['alarm_ts'] = alarm.get('ts')
                if alarm.get('stop'):
                    # No looping alarms implemented in this simple driver
                    pass
                else:
                    file = str(alarm.get('file') or 'red-alert.wav')
                    self._play_sound(file)
        except Exception:
            pass
        try:
            cap = audio.get('cap_launch')
            if cap and (self._last_stamps.get('cap_ts') != cap.get('ts')):
                self._last_stamps['cap_ts'] = cap.get('ts')
                file = str(cap.get('file') or 'SHAR.wav')
                self._play_sound(file, volume=float(cap.get('vol') or 0.25))
        except Exception:
            pass

    def _sound_map(self) -> Dict[str, str]:
        return {
            'exocet_mm38': 'missile_launch.wav',
            'seacat': 'missile_launch.wav',
            'gun_4_5in': '4.5cmgun.wav',
            'oerlikon_20mm': 'gunfire.mp3',
            'gam_bo1_20mm': 'gunfire.mp3',
            'corvus_chaff': 'chaff.wav',
            'weapon_launch': 'missile_launch.wav',
        }

    def _play_sound(self, file: str, *, volume: float = 1.0) -> None:
        try:
            if not self._audio_ready or not self._pg:
                return
            path = os.path.join(self._audio_dir, file)
            snd = self._pg.mixer.Sound(path)
            snd.set_volume(max(0.0, min(1.0, float(volume))))
            snd.play()
        except Exception:
            pass

    # --- NAV helpers
    def _nav_apply_course(self):
        s = self.nav_in_hdg.text().strip()
        if not s:
            return
        try:
            hdg = float(s)
        except Exception:
            return
        self._post('/api/nav/set', {"heading": hdg})

    def _nav_apply_speed(self):
        s = self.nav_in_spd.text().strip()
        if not s:
            return
        try:
            spd = float(s)
        except Exception:
            return
        self._post('/api/nav/set', {"speed": spd})

    def _render_nav_fleet(self, j: Dict[str, Any]):
        try:
            fleet = j.get('ownfleet') or []
            # Preferred order: own ship first, then others as listed
            def _key(u):
                return (0 if str(u.get('id','')) == 'own' or str(u.get('name','')).lower().find('sheffield')>=0 else 1)
            rows = sorted(fleet, key=_key)
            self.tbl_nav_fleet.setRowCount(0)
            for u in rows:
                row = self.tbl_nav_fleet.rowCount(); self.tbl_nav_fleet.insertRow(row)
                name = str(u.get('name','Ship'))
                cell = str(u.get('cell','—'))
                spd = u.get('speed','—')
                hdg = u.get('heading','—')
                vals = [name, cell, str(spd), str(hdg)]
                for col,v in enumerate(vals):
                    it = QTableWidgetItem(v); it.setFlags(Qt.ItemIsEnabled)
                    self.tbl_nav_fleet.setItem(row, col, it)
        except Exception:
            # Leave table as-is on any error
            pass

    # --- CAP helpers
    def _cap_to_cell(self):
        cell = self.cap_cell.text().strip().upper()
        if not cell:
            return
        self._post('/cap/launch_to', {"cell": cell, "station_minutes": 10, "radius_nm": 10})

    # --- Radio helper
    def _radio_send(self):
        s = self.radio_in.text().strip()
        if not s:
            return
        self._post('/radio/ask', {"text": s})
        self.radio_in.setText("")

    # --- ENG helpers
    def _eng_refresh(self):
        j = self._get('/eng/systems')
        if not j or not j.get('ok'):
            self._eng_teams_txt = '—/—'
            return
        teams_total = int(j.get('teams_total', 0)); teams_free = int(j.get('teams_free', 0))
        self._eng_teams_txt = f"{teams_free}/{teams_total}"
        items = j.get('systems') or []
        self.tbl_eng.setRowCount(0)
        for s in items:
            row = self.tbl_eng.rowCount(); self.tbl_eng.insertRow(row)
            sid = str(s.get('id',''))
            status = str(s.get('status',''))
            timer = str(int(s.get('timer_s') or 0))
            team = 'YES' if s.get('team_assigned') else 'NO'
            # cells
            for col, val in enumerate([sid, status, timer, team]):
                item = QTableWidgetItem(val); item.setFlags(Qt.ItemIsEnabled)
                self.tbl_eng.setItem(row, col, item)
            # action button
            btn = QPushButton('Release' if s.get('team_assigned') else 'Assign')
            if s.get('team_assigned'):
                btn.clicked.connect(lambda _=False, sysid=sid: self._eng_release(sysid))
            else:
                btn.clicked.connect(lambda _=False, sysid=sid: self._eng_assign(sysid))
            self.tbl_eng.setCellWidget(row, 4, btn)

    def _eng_assign(self, sysid: str):
        self._post('/eng/assign', {'id': sysid}); self._eng_refresh()

    def _eng_release(self, sysid: str):
        self._post('/eng/release', {'id': sysid}); self._eng_refresh()


def _log_setup() -> str:
    """Create a simple desktop log file and configure logging.
    // Invariant guard: consistency suite — add robust logging bootstrap for desktop app
    """
    try:
        here = Path(__file__).resolve()
        repo_root = here.parents[2]
        log_dir = repo_root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / 'desktop_app.log'
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(message)s',
            handlers=[logging.FileHandler(str(path), encoding='utf-8'), logging.StreamHandler(sys.stderr)],
        )
        return str(path)
    except Exception:
        # Fallback to current directory
        path = Path.cwd() / 'desktop_app.log'
        try:
            logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', filename=str(path))
        except Exception:
            pass
        return str(path)


def _install_excepthook(log_path: str) -> None:
    """Route uncaught exceptions to the log and stderr.
    // Invariant guard: consistency suite — desktop exception hook
    """
    import traceback

    def _hook(etype, value, tb):
        try:
            logging.error("Uncaught exception:")
            logging.error("%s: %s", etype.__name__ if hasattr(etype, '__name__') else str(etype), value)
            for line in traceback.format_tb(tb):
                logging.error(line.rstrip())
        except Exception:
            pass
        try:
            sys.stderr.write(f"\n[desktop_app] Uncaught exception; see log: {log_path}\n")
        except Exception:
            pass
        # Delegate to default hook too
        try:
            sys.__excepthook__(etype, value, tb)  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        sys.excepthook = _hook
    except Exception:
        pass

def main(argv: List[str]) -> int:
    # Robust logging for packaged app
    log_path = _log_setup(); _install_excepthook(log_path)
    # Ensure Qt can find platform plugins (macOS: 'cocoa')
    try:
        try:
            plugins = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
            libs = QLibraryInfo.path(QLibraryInfo.LibraryPath.LibrariesPath)
        except Exception:
            plugins = None; libs = None
        # Set both env and Qt runtime paths for robustness
        if plugins and isinstance(plugins, str):
            try:
                import os as _os
                _os.environ['QT_PLUGIN_PATH'] = plugins
                plat = str(Path(plugins) / 'platforms')
                _os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plat
                # For macOS: ensure frameworks path is visible to the process
                if libs and isinstance(libs, str):
                    _os.environ.setdefault('DYLD_FRAMEWORK_PATH', libs)
                    _os.environ.setdefault('DYLD_LIBRARY_PATH', libs)
            except Exception:
                pass
            # Hard set library paths (overwrites) rather than add/append
            try:
                QCoreApplication.setLibraryPaths([plugins])
            except Exception:
                QCoreApplication.addLibraryPath(plugins)
    except Exception:
        pass
    # Ensure backend server is reachable; if not, start embedded server
    try:
        import time
        port = _env_port(); base = f"http://127.0.0.1:{port}"
        ok = False
        try:
            r = requests.get(base + '/health', timeout=0.4)
            ok = r.ok
        except Exception:
            ok = False
        if not ok:
            try:
                import threading
                from projects.falklandV2 import webdash as _wd
                def _run():
                    try: _wd.app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
                    except Exception: pass
                t = threading.Thread(target=_run, daemon=True); t.start()
                for _ in range(25):
                    time.sleep(0.2)
                    try:
                        if requests.get(base + '/health', timeout=0.25).ok:
                            ok = True; break
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception:
        pass
    app = QApplication(argv)
    # Optional: load Glass_TTY_VT220.ttf if available (set FONT_PATH to override)
    try:
        font_candidates: List[str] = []
        env_fp = os.environ.get('FONT_PATH')
        if env_fp:
            font_candidates.append(env_fp)
        here = Path(__file__).resolve().parent
        font_candidates.append(str(here / 'assets' / 'fonts' / 'Glass_TTY_VT220.ttf'))
        font_candidates.append(str(here / 'static' / 'fonts' / 'Glass_TTY_VT220.ttf'))
        loaded_family: Optional[str] = None
        for fp in font_candidates:
            try:
                p = Path(fp)
                if p.exists():
                    fid = QFontDatabase.addApplicationFont(str(p))
                    fams = QFontDatabase.applicationFontFamilies(fid)
                    if fams:
                        loaded_family = fams[0]
                        break
            except Exception:
                continue
        # Set a readable default font
        try:
            size = int(os.environ.get('FONT_SIZE', '12'))
        except Exception:
            size = 12
        fam = loaded_family or 'Menlo'
        app.setFont(QFont(fam, size))
    except Exception:
        pass
    # Optional: apply CRT-like stylesheet if present
    try:
        here = Path(__file__).resolve().parent
        qss = here / 'assets' / 'style' / 'desktop.qss'
        if qss.exists():
            app.setStyleSheet(qss.read_text(encoding='utf-8'))
    except Exception:
        pass
    base = f"http://127.0.0.1:{_env_port()}"
    win = MainWindow(base)
    # Fullscreen on small panels if requested
    if os.environ.get('FULLSCREEN', '0') == '1':
        win.showFullScreen()
    else:
        win.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
