// app.js — the whole client. Server is the source of truth; we just draw what it sends.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const el = {
    conn: $('conn'), form: $('join-form'), name: $('name'), join: $('join'), me: $('me'),
    wallet: $('wallet'), walletBudget: $('wallet-budget'), walletPhase: $('wallet-phase'), walletShield: $('wallet-shield'),
    idle: $('idle'), idleFill: $('idle-fill'),
    winFrom: $('win-from'), winTo: $('win-to'), angerEmoji: $('anger-emoji'), angerFill: $('anger-fill'),
    clicker: $('clicker'), count: $('count'), combo: $('combo'),
    hand: $('hand'), handIcon: $('hand-icon'), handName: $('hand-name'), handDesc: $('hand-desc'), playCard: $('play-card'),
    newGame: $('new-game'), players: $('players'), online: $('online'), targetHint: $('target-hint'),
    hall: $('hall'), toasts: $('toasts'), safeZone: $('safe-zone'),
    modal: $('modal'), modalText: $('modal-text'), modalOk: $('modal-ok'),
    crash: $('crash'), crashCode: $('crash-code'),
  };

  const CARDS = {
    battery: { icon: '🔋', name: 'Батарейка', desc: (r) => `+${r.battery} кліки до запасу` },
    sabotage: { icon: '🗡', name: 'Саботаж', desc: (r) => `Гравець втрачає ${r.sabotage} кліки`, targeted: true },
    swap: { icon: '🔄', name: 'Обмін', desc: () => 'Поміняйся запасом кліків з гравцем', targeted: true },
    shield: { icon: '🛡', name: 'Щит', desc: () => 'Блокує наступний саботаж, обмін або штраф. Ніхто не знає, що він у тебе є' },
  };
  const cardLabel = (kind) => `${CARDS[kind].icon} ${CARDS[kind].name}`;

  // ------------------------------------------------------------- state
  let state = null;          // last public snapshot
  let you = { player_id: null, card: '', shielded: false };
  let ws = null;
  let gameId = null;
  let targeting = false;     // picking a target for sabotage / swap
  let lockedUntil = 0;       // "lock" trap, performance.now() ms
  let lastAction = null;     // my last click, performance.now() ms (idle timer)
  let displaced = false;     // the button is not where it lives
  let hoverEscaped = false;  // the hover trick happens once per game

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
    if (msg.type === 'notice') return onNotice(msg);
    if (msg.type === 'you') {
      you = msg;
      if (!you.player_id && !el.name.value) el.name.value = you.name || '';
      if (you.locked_ms) lockFor(you.locked_ms);
      if (you.idle_ms != null && lastAction == null) lastAction = performance.now() - you.idle_ms;
      if (!you.player_id) lastAction = null;
      return render();
    }
    if (msg.type === 'state') {
      state = msg.state;
      if (state.game_id !== gameId) { gameId = state.game_id; newRound(); }
      render();
      msg.events.forEach(onEvent);
    }
  }

  const mine = (ev) => ev.player_id && ev.player_id === you.player_id;
  const targetsMe = (ev) => ev.target_id && ev.target_id === you.player_id;

  function onEvent(ev) {
    switch (ev.kind) {
      case 'join': return toast(`👋 ${ev.name} приєднався`);
      case 'new_game': return toast('🔄 Нова гра почалась!', 'good');
      case 'deposit': return mine(ev) ? floater('+1 💰') : null;
      case 'armed':
        if (mine(ev)) floater('⚔️');
        return toast(mine(ev) ? '⚔️ Депозит внесено — ти в бою!' : `⚔️ ${ev.name} вніс депозит і вже в бою`, mine(ev) ? 'good' : '');
      case 'click': bump(); return mine(ev) ? floater('+1') : null;
      case 'idle': return mine(ev) ? toast('😴 −1 клік за простій', 'bad') : null;
      case 'burn': bump(); return mine(ev) ? toast('🔥 Твій клік згорів за простій', 'bad') : null;
      case 'burn_win':
        bump(); shake();
        return toast(mine(ev) ? '🔥🔥 Ти ПРОСПАВ виграшний клік! Його перезагадали' : `🔥 ${ev.name} проспав виграшний клік! Його перезагадали`, 'bad');
      case 'shield':
        if (ev.against === 'idle') return mine(ev) ? toast('🛡 Щит врятував від штрафу за простій', 'good') : null;
        if (targetsMe(ev)) return toast(`🛡 Твій щит відбив ${cardLabel(ev.against)} від ${ev.name}!`, 'good');
        return toast(`🛡 ${ev.target} відбив ${cardLabel(ev.against)} від ${ev.name}`);
      case 'card': return onCardEvent(ev);
      case 'win':
        confetti();
        return showModal(mine(ev) ? `🎉 Ти влучив у виграшний клік №${state.secret}!` : `🎉 Переміг ${ev.name} (клік №${state.secret})`);
      case 'draw':
        return showModal(`🤝 Нічия: кліки закінчились у всіх. Виграшним був №${state.secret}`);
    }
  }

  function onCardEvent(ev) {
    const r = state.rules;
    if (ev.card === 'battery') return toast(mine(ev) ? `🔋 +${r.battery} кліки!` : `🔋 ${ev.name} підзарядився`, mine(ev) ? 'good' : '');
    if (ev.card === 'sabotage') {
      if (targetsMe(ev)) { shake(); return toast(`🗡 ${ev.name} саботував тебе: −${r.sabotage} кліки`, 'bad'); }
      return toast(`🗡 ${ev.name} саботував ${ev.target}`);
    }
    if (ev.card === 'swap') {
      if (targetsMe(ev)) return toast(`🔄 ${ev.name} помінявся з тобою кліками`, 'bad');
      return toast(`🔄 ${ev.name} помінявся кліками з ${ev.target}`);
    }
  }

  function onNotice(n) {
    switch (n.kind) {
      case 'trap':
        if (n.trap === 'slip') { jump(); return toast('🏃 Кнопка вислизнула — клік не зарахувався', 'bad'); }
        if (n.trap === 'modal') return showCrash();
        if (n.trap === 'lock') { lockFor(n.ms); return toast('🔒 Кнопку заклинило', 'bad'); }
        return;
      case 'locked': return lockFor(n.ms);
      case 'card': return toast(`🃏 Нова карта: ${cardLabel(n.card)}`, 'good');
      case 'card_lost': return toast('🃏 Рука повна — нова карта згоріла');
      case 'shield_up': return toast('🛡 Щит піднято. Ніхто про це не знає 🤫', 'good');
    }
  }

  // ------------------------------------------------------------- render
  const me = () => state?.players.find((p) => p.id === you.player_id) || null;

  function render() {
    if (!state) return;
    const r = state.rules;
    const p = me();
    const running = !state.finished;
    const inBattle = p && p.deposited >= r.deposit;

    document.querySelectorAll('[data-rule]').forEach((n) => { n.textContent = r[n.dataset.rule]; });

    // the hidden window and how close the counter is to its end
    const [from, to] = state.window;
    const left = Math.max(0, Math.min(1, (to - state.counter) / r.target_max));
    el.winFrom.textContent = from;
    el.winTo.textContent = to;
    el.angerFill.style.width = `${left * 100}%`;
    el.angerFill.style.background = `hsl(${(1 - left) * 120}, 70%, 45%)`;
    el.angerEmoji.textContent = left > 0.75 ? '🤬' : left > 0.5 ? '😠' : left > 0.25 ? '😐' : '🙂';
    if (state.finished) el.angerEmoji.textContent = '😇';
    el.count.textContent = state.counter;

    // name form
    el.name.disabled = el.join.disabled = !!p || state.finished;
    el.join.textContent = p ? 'Saved!' : 'Save';
    el.me.textContent = p ? `Гравець: ${p.name}${state.starter_id === p.id ? ' 🔥' : ''}` : '';

    // wallet
    el.wallet.hidden = !p;
    if (p) {
      el.walletBudget.textContent = `🔋 ${p.budget} кліків`;
      el.walletPhase.textContent = inBattle ? `⚔️ у бою` : `💰 депозит ${p.deposited}/${r.deposit}`;
      el.walletShield.hidden = !you.shielded;
    }

    // the button
    const locked = performance.now() < lockedUntil;
    el.clicker.disabled = !p || !running || p.budget === 0 || locked;
    el.clicker.textContent = locked ? `🔒 ${Math.ceil((lockedUntil - performance.now()) / 1000)}`
      : !p ? 'Click me'
      : p.budget === 0 ? '🪫 кліки скінчились'
      : inBattle ? '⚔️ КЛІК!' : `💰 Депозит ${p.deposited}/${r.deposit}`;
    el.clicker.classList.toggle('big-btn--battle', !!inBattle);

    // card in hand
    const card = p && running && you.card ? CARDS[you.card] : null;
    el.hand.hidden = !card;
    if (card) {
      el.handIcon.textContent = card.icon;
      el.handName.textContent = card.name;
      el.handDesc.textContent = card.desc(r);
      el.playCard.textContent = targeting ? 'Скасувати' : 'Зіграти';
    }
    if (!card) targeting = false;
    el.targetHint.hidden = !targeting;

    renderPlayers(r);
    el.hall.replaceChildren(...(state.hall_of_fame.length
      ? state.hall_of_fame.map((g) => row(g.name, `${g.seconds}s`))
      : [empty('Тут буде твоє ім\'я')]));
  }

  function renderPlayers(r) {
    const online = state.players.filter((p) => p.online).length;
    el.online.textContent = state.players.length ? `${online} онлайн` : '';
    if (!state.players.length) return el.players.replaceChildren(empty('Поки нікого. Будь першим!'));
    el.players.replaceChildren(...state.players.map((p) => {
      const isMe = p.id === you.player_id;
      const status = p.deposited >= r.deposit ? `⚔️${p.clicks}` : `💰${p.deposited}/${r.deposit}`;
      const name = `${p.id === state.starter_id ? '🔥 ' : ''}${p.name}${p.has_card ? ' 🃏' : ''}`;
      const node = row(name, `${status} · 🔋${p.budget}`, [isMe && 'is-me', !p.online && 'is-off']);
      if (targeting && !isMe) {
        node.classList.add('targetable');
        node.addEventListener('click', () => {
          send({ type: 'play_card', target: p.id });
          targeting = false;
          render();
        });
      }
      return node;
    }));
  }

  function row(name, value, classes = []) {
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

  el.playCard.addEventListener('click', () => {
    if (!you.card) return;
    if (CARDS[you.card].targeted) {
      targeting = !targeting;
      return render();
    }
    send({ type: 'play_card' });
  });

  let comboCount = 0, comboTimer = null;
  el.clicker.addEventListener('click', () => {
    if (el.clicker.disabled) return;
    hoverEscaped = true; // you caught it, it stops running away on hover
    if (displaced) goHome();
    lastAction = performance.now();
    send({ type: 'click' });

    comboCount++;
    clearTimeout(comboTimer);
    comboTimer = setTimeout(() => { comboCount = 0; el.combo.textContent = ''; }, 450);
    if (comboCount >= 5) {
      el.combo.textContent = `COMBO x${comboCount}!`;
      el.combo.classList.remove('pop'); void el.combo.offsetWidth; el.combo.classList.add('pop');
    }
    if (comboCount && comboCount % 20 === 0) shake();
  });

  function lockFor(ms) {
    lockedUntil = performance.now() + ms;
    render();
  }

  function newRound() {
    hoverEscaped = false;
    targeting = false;
    lockedUntil = 0;
    lastAction = null;
    goHome();
  }

  // ------------------------------------------------------------- the runaway button
  function overlaps(a, b) { return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom); }

  function jump() {
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
    displaced = true;
  }
  function goHome() {
    Object.assign(el.clicker.style, { position: '', left: '', top: '' });
    el.clicker.classList.remove('runaway');
    displaced = false;
  }
  function hoverEscape() {
    if (hoverEscaped || el.clicker.disabled) return;
    hoverEscaped = true;
    jump();
    toast('Хе-хе, спіймай мене 😜');
  }
  el.clicker.addEventListener('pointerenter', (e) => e.pointerType === 'mouse' && hoverEscape());
  el.clicker.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'touch' && !hoverEscaped) { e.preventDefault(); hoverEscape(); }
  });

  // ------------------------------------------------------------- idle bar + lock countdown
  setInterval(() => {
    const p = me();
    const show = p && !state.finished && p.budget > 0 && lastAction != null;
    el.idle.hidden = !show;
    if (show) {
      const idleMs = state.rules.idle_seconds * 1000;
      let elapsed = performance.now() - lastAction;
      if (elapsed >= idleMs) { lastAction += idleMs * Math.floor(elapsed / idleMs); elapsed %= idleMs; }
      const left = 1 - elapsed / idleMs;
      el.idleFill.style.width = `${left * 100}%`;
      el.idle.classList.toggle('idle--danger', left < 0.4);
    }
    if (lockedUntil && performance.now() >= lockedUntil) { lockedUntil = 0; render(); }
    else if (lockedUntil) render();
  }, 100);

  // ------------------------------------------------------------- juice
  function bump() {
    el.count.classList.remove('bump'); void el.count.offsetWidth; el.count.classList.add('bump');
  }

  function floater(text) {
    const f = document.createElement('div');
    f.className = 'floater';
    f.textContent = text;
    const r = el.clicker.getBoundingClientRect();
    f.style.left = `${r.left + r.width / 2 + (Math.random() * 40 - 20)}px`;
    f.style.top = `${r.top}px`;
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
    while (el.toasts.children.length > 4) el.toasts.firstChild.remove();
    setTimeout(() => t.classList.add('out'), 2500);
    setTimeout(() => t.remove(), 3000);
  }

  function showModal(text) { el.modalText.textContent = text; el.modal.hidden = false; el.modalOk.focus(); }
  function hideModal() { el.modal.hidden = true; }
  el.modalOk.addEventListener('click', hideModal);

  const CRASH_CODES = ['0xDEADBEEF: anger overflow', 'NullPointerException at chill.java:42',
    'Segmentation fault (core dumped)', 'ERR_PATIENCE_EXHAUSTED', 'TypeError: calm is not a function'];
  function showCrash() {
    el.crashCode.textContent = CRASH_CODES[Math.floor(Math.random() * CRASH_CODES.length)];
    el.crash.hidden = false;
  }
  el.crash.addEventListener('pointerdown', (e) => { e.preventDefault(); el.crash.hidden = true; });

  document.addEventListener('keydown', (e) => {
    if (!el.crash.hidden) { e.preventDefault(); el.crash.hidden = true; return; }
    if (!el.modal.hidden && (e.key === 'Enter' || e.key === 'Escape')) { e.preventDefault(); hideModal(); return; }
    if (targeting && e.key === 'Escape') { targeting = false; render(); }
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
