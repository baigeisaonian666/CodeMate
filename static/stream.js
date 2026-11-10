// Incremental UTF-8 + SSE decoding; network chunks need not align with events.
async function consumeChatStream(response, onEvent) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Error(typeof data.detail === 'string' ? data.detail : '请求失败');
  }
  if (!response.body) throw Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', result = null;
  function drain() {
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = frame.split('\n').filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart()).join('\n');
      if (!data) continue;
      const event = JSON.parse(data);
      if (event.type === 'error') throw Error(event.detail || '生成中断');
      onEvent(event);
      if (event.type === 'done') result = event;
    }
  }
  try {
    while (!result) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      drain();
      if (done) break;
    }
    if (!result) throw Error('连接提前结束，回答尚未完成，请重试');
    return result;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
