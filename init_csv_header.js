// Initialize 5minelecNEW.csv with headers if empty or missing
// This function node should run on startup to ensure the file exists with proper headers

const fs = require('fs');
const path = '/share/file_notifications/5minelecNEW.csv';
const headers = 'created,cumOff,cumSh,cumPk,cumExp,col5,col6,col7,col8,col9,col10,aemo,periodEnding,col13';

try {
  const stat = fs.statSync(path);
  if (stat.size === 0) {
    fs.writeFileSync(path, headers + String.fromCharCode(10), 'utf8');
    node.warn('Initialized 5minelecNEW.csv with headers (file was empty)');
  }
} catch(e) {
  // File doesn't exist, create it with headers
  fs.writeFileSync(path, headers + String.fromCharCode(10), 'utf8');
  node.warn('Created 5minelecNEW.csv with headers (file did not exist)');
}

return null;
