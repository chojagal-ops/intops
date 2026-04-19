(function () {
  document.addEventListener('DOMContentLoaded', function () {
    const topbar = document.querySelector('.topbar');
    if (!topbar) return;

    const nav = document.createElement('div');
    nav.className = 'hist-nav';
    nav.innerHTML =
      '<button class="hist-btn" onclick="history.back()" title="이전 페이지">&#8592; 이전</button>' +
      '<button class="hist-btn" onclick="history.forward()" title="다음 페이지">다음 &#8594;</button>';

    const ref = topbar.querySelector('.topbar-nav') || topbar.querySelector('.topbar-user');
    if (ref) topbar.insertBefore(nav, ref);
    else topbar.appendChild(nav);
  });
})();
