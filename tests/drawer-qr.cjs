// Run: QPAI_UI_MODULES=/path/to/node_modules node tests/drawer-qr.cjs
const {createRequire} = require('node:module');
const req = createRequire((process.env.QPAI_UI_MODULES || process.cwd() + '/node_modules') + '/package.json');
const {JSDOM} = req('jsdom');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {url:'http://localhost', runScripts:'outside-only'});
for (const key of ['window','document','navigator','HTMLElement','HTMLInputElement','Element','Node','MutationObserver','Event','ShadowRoot','SVGElement']) global[key] = dom.window[key];
global.getComputedStyle = dom.window.getComputedStyle;
window.matchMedia = () => ({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
window.requestAnimationFrame = fn => setTimeout(fn, 0);
window.cancelAnimationFrame = id => clearTimeout(id);
const React = req('react');
const {createRoot} = req('react-dom/client');
const {flushSync} = req('react-dom');
const {Form, Input} = req('antd');
const h = React.createElement;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const timeout = window.setTimeout.bind(window);
window.setTimeout = (fn, ms) => timeout(fn, ms === 5000 ? 10 : ms);
const root = createRoot(document.getElementById('root'));
let formApi, saved, requests = [], response;
function Drawer({plugin=true}) {
 const [form] = Form.useForm(); formApi = form;
 return h('div',{role:'dialog'},h(Form,{form,onFinish:v=>{saved=v;}},
   h('p',null,plugin?'钉钉 AI · 单卡对话：支持官方扫码授权、流式思考与工具审批。':'官方钉钉渠道'),
   h(Form.Item,{name:'client_id'},h(Input)),
   h(Form.Item,{name:'client_secret'},h(Input.Password)),
   h(Form.Item,{name:'card_template_id'},h(Input))));
}
window.QwenPaw = {host:{React,antd:req('antd'),fetch:async path=>{
 requests.push(path);return {ok:true,json:async()=>response(path)};
}},registerRoutes(){}};
const source = fs.readFileSync(require('node:path').join(__dirname,'../ui/index.js'),'utf8');
async function open(plugin=true) {
 flushSync(()=>root.render(null)); await delay(15);
 flushSync(()=>root.render(h(Drawer,{plugin}))); await delay(15);
}
function click() { const b=document.querySelector('[data-qpai-qr] button'); assert.ok(b);b.click(); }
(async()=>{
 window.eval(source);
 response = path=>path.includes('/status?')?{status:'success',credentials:{client_id:'ding-test',client_secret:'test-secret'}}:{qrcode_img:'test-image',poll_token:'test-token'};
 await open(); click(); await delay(80);
 assert.equal(formApi.getFieldValue('client_id'),'ding-test');
 assert.equal(formApi.getFieldValue('client_secret'),'test-secret');
 formApi.submit(); await delay(20);
 assert.equal(saved.client_id,'ding-test');assert.equal(saved.client_secret,'test-secret');
 assert.match(document.querySelector('[role="status"]').textContent,/已填入凭据/);
 assert.equal(document.querySelector('[data-qpai-qr] img').getAttribute('src'),null);
 console.log('PASS: official QR success fills and saves actual AntD Form values');
 await open(false);assert.equal(document.querySelector('[data-qpai-qr]'),null);
 console.log('PASS: official and unrelated channel forms are not modified');
 let resolveQR;
 response = ()=>new Promise(resolve=>{resolveQR=resolve;});
 await open();click();await delay(15);await open();
 resolveQR({qrcode_img:'old',poll_token:'old'});await delay(40);
 assert.equal(formApi.getFieldValue('client_id'),undefined);
 assert.equal(document.querySelector('[data-qpai-qr] img').getAttribute('src'),null);
 console.log('PASS: closing/reopening discards stale authorization');
 response = path=>path.includes('/status?')?{status:'expired'}:{qrcode_img:'test-image',poll_token:'test-token'};
 click();await delay(50);assert.match(document.querySelector('[role="status"]').textContent,/已过期/);
 assert.equal(document.querySelector('[data-qpai-qr] img').getAttribute('src'),null);
 console.log('PASS: expired QR removed with retry guidance');
 response = path=>path.includes('/status?')?{status:'success',credentials:{client_id:'incomplete'}}:{qrcode_img:'test-image',poll_token:'test-token'};
 click();await delay(50);assert.match(document.querySelector('[role="status"]').textContent,/未返回完整凭据/);
 assert.equal(formApi.getFieldValue('client_id'),undefined);
 console.log('PASS: incomplete credentials never partially overwrite the form');
 window.eval(source);await delay(20);assert.equal(document.querySelectorAll('[data-qpai-qr]').length,1);
 console.log('PASS: repeated plugin load does not duplicate QR controls');
 window.__qpaiDrawerCleanup();root.unmount();dom.window.close();
})().catch(e=>{console.error(e);process.exitCode=1;window.__qpaiDrawerCleanup?.();root.unmount();dom.window.close();});
