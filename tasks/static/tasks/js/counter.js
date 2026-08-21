//counter.js
const clickBtn = document.getElementById('clicker_button');
const count = document.getElementById('count');
const newGameButton = document.getElementById('new-game-button');

let gameOver = false;

clickBtn.addEventListener('click', async () => {
    if (gameOver) {
        showModal('Гра завершена, тисни "Нова гра"');
        return;
    }
    if (!window.currentUserName) {
        newGameButton.disabled = true;
        showModal("Спочатку збережи ім'я");
        return;
    }

    window.markGameStarted();  // втеча кнопки більше не повториться

    const localCounter = parseInt(count.textContent) || 0;
    if (localCounter % 7 === Math.floor(Math.random() * 3) + 1) {
        showModal('Підтвердити операцію: Enter');
    }

    const response = await fetch('/click/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
    });
    const data = await response.json();

    if (!response.ok) {
        showModal(data.error || 'Помилка');
        return;
    }

    count.textContent = data.counter_value;  // п.5 — оновлення без reload

    if (data.is_won) {
        gameOver = true;
        showModal(`🎉 Вітаю! Переміг: ${data.winner_name}!`);
    }
});

newGameButton.addEventListener('click', async () => {
    const response = await fetch('/new-game/', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
    });
    const data = await response.json();

    if (!response.ok) {
        showModal(data.error || 'Не вдалось почати нову гру');
        return;
    }

    gameOver = false;
    count.textContent = '0';
    document.getElementById('save_button').disabled = false;
    document.getElementById('save_placeholder').disabled = false;
    document.getElementById('save_placeholder').value = '';
    document.getElementById('save_button').textContent = 'Save';
    document.getElementById('player-name-display').textContent = '';
    window.currentUserName = null;
    window.resetClickerButton();
    showModal('Нова гра почалась!');
});
