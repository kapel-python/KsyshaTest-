const checkData = {
  ok: true,
  match: false,
  // token_user: {name: 'Bob'},
  // current_user: {name: 'Alice'}
};
try {
  const html = `
    <h2 style="margin-top:0; font-size:22px; margin-bottom: 24px;">Account Switch</h2>
    <div style="background:var(--bg-secondary, #f5f5f5); padding:16px; border-radius:12px; margin-bottom:16px; text-align:left;">
      <div style="font-size:14px; opacity:0.7; margin-bottom:4px;">Current session:</div>
      <div style="font-weight:bold; font-size:18px;">${checkData.current_user.name}</div>
    </div>
  `;
  console.log("SUCCESS");
} catch(e) {
  console.log("ERROR", e.message);
}
