import { createFilm } from './film.js';

const response = await fetch('./timing.json');
if (!response.ok) throw new Error(`Narration timing failed to load: ${response.status}`);
const timing = await response.json();
const film = await createFilm(timing);
const query = new URLSearchParams(location.search);
window.DUR = timing.duration_sec;
window.render = time => film.render(time, query.has('thumbnail'));
window.READY = true;
