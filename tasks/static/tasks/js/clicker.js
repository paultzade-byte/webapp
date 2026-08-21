// clicker.js

// ---- CSRF і модалка (спільні хелпери) ----
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}
window.getCookie = getCookie;

function showModal(text) {
    document.getElementById('modal-text').textContent = text;
    document.getElementById('modal-overlay').style.display = 'flex';
}
window.showModal = showModal;

document.getElementById('modal-close').addEventListener('click', () => {
    document.getElementById('modal-overlay').style.display = 'none';
});
// ---- Втікаюча кнопка: тільки до першого кліку по грі ----
const btn = document.getElementById('clicker_button');
const protectedZone = document.getElementById('not-hovered-zone');
let hoverCount = 0;
let caught = false;
let gameStarted = window.INITIAL_STATE.gameStarted;
const MAX_ESCAPES = 1;
// ---- Save name ----
const nameForm = document.getElementById('name-form');
const nameInput = document.getElementById('save_placeholder');
const saveButton = document.getElementById('save_button');
const nameDisplay = document.getElementById('player-name-display');

window.currentUserName = window.INITIAL_STATE.nameSaved ? nameInput.value : null;
btn.disabled = !window.currentUserName;

nameForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const name = nameInput.value.trim();
    if (!name) {
        showModal("Введи ім'я перед збереженням");
        return;
    }

    const formData = new FormData();
    formData.append('user_name', name);

    const response = await fetch('/save-name/', {
        method: 'POST',
        body: formData,
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
    });
    const data = await response.json();

    if (!response.ok) {
        showModal(data.error || "Не вдалось зберегти ім'я");
        return;
    }

    window.currentUserName = data.user_name;
    btn.disabled = !window.currentUserName;
    saveButton.disabled = true;
    saveButton.textContent = 'Saved!';
    nameInput.disabled = true;
    nameDisplay.textContent = `Гравець: ${data.user_name}`;
});


function rectsOverlap(a, b) {
    return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
}

function getRandomPosition(w, h) {
    const maxX = window.innerWidth - w;
    const maxY = window.innerHeight - h;
    const zoneRect = protectedZone.getBoundingClientRect();
    let x, y, candidateRect, attempts = 0;
    do {
        x = Math.random() * maxX;
        y = Math.random() * maxY;
        candidateRect = { left: x, right: x + w, top: y, bottom: y + h };
        attempts++;
    } while (rectsOverlap(candidateRect, zoneRect) && attempts < 30);
    return { x, y };
}

function tryEscape() {
    if (!window.currentUserName) return;
    if (gameStarted || hoverCount >= MAX_ESCAPES) return;  // тільки на старті, один раз
    hoverCount++;
    btn.style.position = 'fixed';
    const { x, y } = getRandomPosition(btn.offsetWidth, btn.offsetHeight);
    btn.style.left = `${x}px`;
    btn.style.top = `${y}px`;
}

btn.addEventListener('pointerenter', (e) => {
  if (e.pointerType === 'mouse') tryEscape();
});          // desktop
btn.addEventListener('pointerdown', (e) => {              // mobile
  if (e.pointerType === 'touch') tryEscape(); // first touch
  if (hoverCount >= MAX_ESCAPES && !caught) {
    showModal('Нарешті! Ти впіймав кнопку 🎉');
    caught = true;
  }
});

window.markGameStarted = () => { gameStarted = true; };  // викликає counter.js при першому кліку

window.resetClickerButton = () => {
  hoverCount = 0;
  caught = false;
  gameStarted = false;
  btn.style.position = '';
  btn.style.left = '';
  btn.style.top = '';
  btn.disabled = true;
};

document.addEventListener('keydown', (e) => {
  const modal = document.getElementById('modal-overlay');
  if (modal.style.display === 'none') return;

  if (e.key === 'Enter' || e.key === 'Escape') {
    e.preventDefault();
    modal.style.display = 'none';
  }
});
