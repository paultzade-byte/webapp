// app.js — the whole client. Server is the source of truth; we just draw what it sends.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = {
    conn: $('conn'), target: $('target'), form: $('join-form'), name: $('name'), join: $('join'),
    me: $('me'), angerPct: $('anger-pct'), angerEmoji: $('anger-emoji'), angerFill: $('anger-fill'),
    clicker: $('clicker'), count: $('count'), combo: $('combo'), newGame: $('new-game'),
    players: $('players'), online: $('online'), hall: $('hall'), toasts: $('toasts'),
    modal: $('modal'), modalText: $('modal-text'), modalOk: $('modal-ok'), safeZone: $('safe-zone'),
  };

  // ------------------------------------------------------------- state
  let state = null;         // last snapshot from the server
  let myId = null;          // my public player id in the current game (null = not joined)
  let ws = null;
  let escaped = false;      // the button runs away once per game
  let gameId = null;

  // ------------------------------------------------------------- socket
  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/game/`);
    ws.onopen = () => setConn(true);
    ws.onclose = () => { setConn(false); setTimeout(connect, 1500); };
    ws.onmessage = (e) => handle(JSON.parse(e.data));
  }
  const send = (msg) => ws && ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(msg));
  setInterval(() => send({ type: 'ping' }), 10000);

  function setConn(on) {
    el.conn.textContent = on ? '● online' : '● reconnecting…';
    el.conn.className = 'conn ' + (on ? 'conn--on' : 'conn--off');
  }

  function handle(msg) {
    if (msg.type === 'error') return toast(msg.message, 'bad');
    if (msg.type === 'you') {
      myId = msg.player_id;
      if (!myId && !el.name.value) el.name.value = msg.name || '';
      return render();
    }
    if (msg.type === 'state') {
      state = msg.state;
      if (state.game_id !== gameId) { gameId = state.game_id; resetButton(); }
      render();
      if (msg.event) onEvent(msg.event);
    }
  }

  function onEvent(ev) {
    if (ev.kind === 'join') toast(`👋 ${ev.name} приєднався`);
    if (ev.kind === 'new_game') toast('🔄 Нова гра почалась!', 'good');
    if (ev.kind === 'win') {
      confetti();
      showModal(ev.name === me()?.name ? '🎉 Ти переміг! Злість — нуль.' : `🎉 Переміг ${ev.name}!`);
    }
    if (ev.kind === 'click' && ev.player_id !== myId) bump(false);
  }

  // ------------------------------------------------------------- render
  const me = () => state?.players.find((p) => p.id === myId) || null;

  function render() {
    if (!state) return;
    const joined = !!me();
    const pct = Math.max(0, Math.round(100 * (1 - state.counter / state.target)));

    el.target.textContent = state.target;
    el.count.textContent = state.counter;
    el.angerPct.textContent = pct + '%';
    el.angerFill.style.width = pct + '%';
    el.angerFill.style.background = `hsl(${(100 - pct) * 1.2}, 70%, 45%)`;
    el.angerEmoji.textContent = pct > 75 ? '🤬' : pct > 50 ? '😠' : pct > 25 ? '😐' : pct > 0 ? '🙂' : '😇';

    el.name.disabled = el.join.disabled = joined || state.finished;
    el.join.textContent = joined ? 'Saved!' : 'Save';
    el.me.textContent = joined
      ? `Гравець: ${me().name}${state.starter_id === myId ? ' 🔥 (ти розпалив цю гру)' : ''}`
      : '';
    el.clicker.disabled = !joined || state.finished;

    const online = state.players.filter((p) => p.online).length;
    el.online.textContent = state.players.length ? `${online} онлайн` : '';
    el.players.replaceChildren(...(state.players.length
      ? state.players.map((p) => li(
          `${p.id === state.starter_id ? '🔥 ' : ''}${p.name}`, p.clicks,
          [p.id === myId && 'is-me', !p.online && 'is-off']))
      : [empty('Поки нікого. Будь першим!')]));
    el.hall.replaceChildren(...(state.hall_of_fame.length
      ? state.hall_of_fame.map((g) => li(g.name, `${g.seconds}s`))
      : [empty('Тут буде твоє ім\'я')]));
  }

  function li(name, value, classes = []) {
    const node = document.createElement('li');
    node.className = classes.filter(Boolean).join(' ');
    const a = document.createElement('span'); a.textContent = name;
    const b = document.createElement('b'); b.textContent = value;
    node.append(a, b);
    return node;
  }
  function empty(text) { const n = document.createElement('li'); n.className = 'empty'; n.textContent = text; return n; }

  // ------------------------------------------------------------- actions
  el.form.addEventListener('submit', (e) => {
    e.preventDefault();
    send({ type: 'join', name: el.name.value });
  });

  el.newGame.addEventListener('click', () => send({ type: 'new_game' }));

  let comboCount = 0, comboTimer = null;
  el.clicker.addEventListener('click', (e) => {
    if (el.clicker.disabled) return;
    escaped = true; // after the first real click, it stops running away
    send({ type: 'click' });
    bump(true, e);

    comboCount++;
    clearTimeout(comboTimer);
    comboTimer = setTimeout(() => { comboCount = 0; el.combo.textContent = ''; }, 450);
    if (comboCount >= 5) {
      el.combo.textContent = `COMBO x${comboCount}!`;
      el.combo.classList.remove('pop'); void el.combo.offsetWidth; el.combo.classList.add('pop');
    }
    if (comboCount && comboCount % 20 === 0) shake();

    // a tiny bit of bureaucratic chaos, like in the original
    if (Math.random() < 0.03) showModal('Підтвердити операцію: Enter');
  });

  // ------------------------------------------------------------- the runaway button
  function overlaps(a, b) { return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom); }

  function runAway() {
    if (escaped || el.clicker.disabled) return;
    escaped = true;
    const w = el.clicker.offsetWidth, h = el.clicker.offsetHeight;
    const zone = el.safeZone.getBoundingClientRect();
    let x, y, tries = 0;
    do {
      x = Math.random() * (innerWidth - w);
      y = Math.random() * (innerHeight - h);
      tries++;
    } while (overlaps({ left: x, right: x + w, top: y, bottom: y + h }, zone) && tries < 30);
    Object.assign(el.clicker.style, { position: 'fixed', left: `${x}px`, top: `${y}px` });
    el.clicker.classList.add('runaway');
    toast('Хе-хе, спіймай мене 😜');
  }
  function resetButton() {
    escaped = false;
    Object.assign(el.clicker.style, { position: '', left: '', top: '' });
    el.clicker.classList.remove('runaway');
  }
  el.clicker.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && runAway());
  el.clicker.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'touch' && !escaped) { e.preventDefault(); runAway(); }
  });

  // ------------------------------------------------------------- juice
  function bump(mine, e) {
    el.count.classList.remove('bump'); void el.count.offsetWidth; el.count.classList.add('bump');
    if (!mine) return;
    const f = document.createElement('div');
    f.className = 'floater';
    f.textContent = '+1';
    const r = el.clicker.getBoundingClientRect();
    f.style.left = `${(e?.clientX || r.left + r.width / 2) + (Math.random() * 30 - 15)}px`;
    f.style.top = `${(e?.clientY || r.top)}px`;
    document.body.append(f);
    setTimeout(() => f.remove(), 800);
  }

  function shake() {
    document.body.classList.remove('shake'); void document.body.offsetWidth; document.body.classList.add('shake');
  }

  function toast(text, kind = '') {
    const t = document.createElement('div');
    t.className = `toast ${kind}`;
    t.textContent = text;
    el.toasts.append(t);
    setTimeout(() => t.classList.add('out'), 2500);
    setTimeout(() => t.remove(), 3000);
  }

  function showModal(text) { el.modalText.textContent = text; el.modal.hidden = false; el.modalOk.focus(); }
  function hideModal() { el.modal.hidden = true; }
  el.modalOk.addEventListener('click', hideModal);
  document.addEventListener('keydown', (e) => {
    if (!el.modal.hidden && (e.key === 'Enter' || e.key === 'Escape')) { e.preventDefault(); hideModal(); }
  });

  // Hand-rolled confetti, no libraries.
  function confetti() {
    const c = $('confetti'), ctx = c.getContext('2d');
    c.width = innerWidth; c.height = innerHeight;
    const bits = Array.from({ length: 180 }, () => ({
      x: innerWidth / 2, y: innerHeight / 3,
      vx: (Math.random() - 0.5) * 16, vy: Math.random() * -14 - 4,
      s: Math.random() * 6 + 4, r: Math.random() * Math.PI, vr: Math.random() * 0.3,
      color: `hsl(${Math.random() * 360}, 90%, 60%)`,
    }));
    let frames = 0;
    (function tick() {
      ctx.clearRect(0, 0, c.width, c.height);
      for (const b of bits) {
        b.vy += 0.35; b.x += b.vx; b.y += b.vy; b.r += b.vr;
        ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.r);
        ctx.fillStyle = b.color; ctx.fillRect(-b.s / 2, -b.s / 4, b.s, b.s / 2);
        ctx.restore();
      }
      if (++frames < 200) requestAnimationFrame(tick); else ctx.clearRect(0, 0, c.width, c.height);
    })();
  }

  connect();
})();
