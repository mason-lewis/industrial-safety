import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
declare const process: {env: Record<string, string | undefined>};
// VITE_API_TARGET points the dev server at an isolated API (for example an --offline workspace).
export default defineConfig({plugins:[react()],server:{host:'127.0.0.1',proxy:{'/api':{target:process.env.VITE_API_TARGET??'http://127.0.0.1:8000',ws:true}}}});
