// Test only. No network access. Every 30 s it stores the time, the computer's
// idle state (same call and 60 s threshold as FriendJo Rhythm) and how many
// browser windows are open. 20 s after install it closes every window, like a
// person clicking X on the last window.
async function record(source) {
  const state = await chrome.idle.queryState(60);
  const windows = (await chrome.windows.getAll()).length;
  const { ticks = [] } = await chrome.storage.local.get('ticks');
  ticks.push({ t: Date.now(), s: state, w: windows, src: source });
  await chrome.storage.local.set({ ticks });
}
function start() { chrome.alarms.create('tick', { periodInMinutes: 0.5 }); }
chrome.runtime.onInstalled.addListener(() => {
  start();
  record('installed');
  setTimeout(async () => {
    const all = await chrome.windows.getAll();
    await record('closing windows');
    for (const w of all) await chrome.windows.remove(w.id);
  }, 20000);
});
chrome.runtime.onStartup.addListener(() => { start(); record('browser started'); });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'tick') record('tick'); });
