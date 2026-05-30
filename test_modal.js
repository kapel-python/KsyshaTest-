const checkData = {ok: true, match: false, token_user: {name: "B"}, current_user: {name: "A"}};
console.log(`
  <div style="font-weight:bold; font-size:18px;">${checkData.current_user.name}</div>
`);
