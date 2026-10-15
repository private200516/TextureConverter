"""Bounded worker-to-UI mailbox. Full logs remain in the backend report."""
from collections import deque
import threading


class UiEventBuffer:
    def __init__(self, capacity=512):
        self.capacity=max(1,int(capacity)); self._logs=deque(maxlen=self.capacity)
        self._progress=None; self._omitted=0; self._lock=threading.Lock()
        self.warnings=0; self.errors=0

    def log(self,level,path,message=''):
        # Only the display is bounded; authoritative backend logging is independent.
        event=(str(level),str(path)[:2048],str(message)[:4096])
        with self._lock:
            if level=='WARNING':self.warnings+=1
            elif level=='ERROR':self.errors+=1
            if len(self._logs)==self.capacity:self._omitted+=1
            self._logs.append(event)

    def progress(self,done,total,name):
        with self._lock:self._progress=(int(done),int(total),str(name)[:2048])

    def drain(self,limit=80):
        with self._lock:
            logs=[self._logs.popleft() for _ in range(min(max(0,int(limit)),len(self._logs)))]
            result=dict(logs=logs,progress=self._progress,omitted=self._omitted,
                        warnings=self.warnings,errors=self.errors,remaining=len(self._logs))
            self._progress=None;self._omitted=0
        return result

    def pending(self):
        with self._lock:return len(self._logs)
