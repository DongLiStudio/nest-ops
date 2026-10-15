import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector',ROOT/'collectors/linux/collector.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class CollectorTests(unittest.TestCase):
    def test_absent_docker_unknown(self):
        with patch.object(m.shutil,'which',return_value=None),patch.object(m.Path,'exists',return_value=False):
            data=m.collect({'services':[{'id':'demo:web','containers':['missing']}]})
        self.assertEqual(data['services']['demo:web']['state'],'unknown')
    def test_stopped_registered_service_down(self):
        from subprocess import CompletedProcess
        with patch.object(m.shutil,'which',return_value='/usr/bin/docker'),patch.object(m,'run',return_value=CompletedProcess([],0,'web|exited|Exited')):
            data=m.collect({'services':[{'id':'demo:web','containers':['web']}]})
        self.assertEqual(data['services']['demo:web']['state'],'down')
    def test_unhealthy_is_degraded(self):
        from subprocess import CompletedProcess
        with patch.object(m.shutil,'which',return_value='/usr/bin/docker'),patch.object(m,'run',return_value=CompletedProcess([],0,'web|running|Up (unhealthy)')):
            data=m.collect({'services':[{'id':'demo:web','containers':['web']}]})
        self.assertEqual(data['services']['demo:web']['state'],'degraded')
if __name__=='__main__':unittest.main()
