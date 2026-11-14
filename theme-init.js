try {
  const savedTheme = localStorage.getItem('aidweave-theme');
  document.documentElement.dataset.theme = savedTheme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
} catch {
  document.documentElement.dataset.theme = 'light';
}
