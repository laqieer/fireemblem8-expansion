"""Focused real namespace controls; no candidate imports or privileged host setup."""

import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from scripts.ci_calibration import kernel, policy, runtime_view, supervisor


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = runtime_view.PROGRAMS


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ci180-runtime-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_actual_host_alias_chains_are_root_owned_and_not_guessed(self):
        observed = runtime_view.observe(RUNTIME)
        runtime_view.validate(observed)
        for name in ("/usr/bin/cc", "/usr/bin/c++"):
            self.assertTrue(any(link["path"].startswith("/etc/alternatives/") for link in observed["programs"][name]["links"]))
            self.assertEqual(observed["programs"][name]["resolved"], str(Path(name).resolve()))
        for defect in ("owner", "target", "object"):
            changed = copy.deepcopy(observed)
            if defect == "owner":
                changed["programs"]["/usr/bin/cc"]["identity"][3] = 999
            elif defect == "target":
                changed["programs"]["/usr/bin/cc"]["links"][0]["target"] = "/usr/bin/false"
            else:
                changed["programs"]["/usr/bin/cc"]["identity"][1] += 1
            with self.subTest(defect=defect), self.assertRaises(policy.GuardError):
                runtime_view.validate(changed)
                runtime_view.compare(observed, changed)

    def private_view(self, *, alternatives, nested):
        directory = self.root / f"view-{alternatives}-{nested}"
        directory.mkdir()
        code = r'''
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import entry,kernel,runtime_view,worker
base=Path(sys.argv[2]); root=base/"root"; root.mkdir()
paths=json.loads(sys.argv[5])
before=runtime_view.observe(paths)
config={"scope":"owned-local-nested","uid":os.getuid(),"gid":os.getgid(),
        "cgroup_relative":kernel.membership(),"host_namespaces":kernel.namespaces()}
group=Path("/sys/fs/cgroup")/kernel.membership().lstrip("/")
config["memory_max"]=kernel.read(group/"memory.max").decode().strip()
config["pids_max"]=kernel.read(group/"pids.max").decode().strip()
source=base/"config.json";source.write_text(json.dumps(config));source.chmod(0o444)
work=base/"work";work.mkdir()
for name in ("usr","etc/alternatives","proc","diag","guard","work"):
    (root/name).mkdir(parents=True,exist_ok=True)
(root/"guard/config.json").touch()
for name in runtime_view.ROOT_ALIASES:
    (root/name).symlink_to(os.readlink("/"+name))
ns=entry.load_namespace_helper(sys.argv[3])
ns.bind(root,root,executable=True);ns.bind("/usr",root/"usr",executable=True)
ns.bind(sys.argv[1],root/"diag")
ns.bind(source,root/"guard/config.json")
ns.bind(work,root/"work",writable=True,executable=True)
if sys.argv[4]=="yes": ns.bind("/etc/alternatives",root/"etc/alternatives")
ns.mount("proc",root/"proc",2|4|8,"proc")
kernel.pivot_root(root);kernel.prctl(38,1)
result={"before":before,"outer_uid_map":kernel.read("/proc/self/uid_map").decode().split()}
try: result["inside"]=runtime_view.observe(paths)
except FileNotFoundError as error: result["missing"]=error.filename
if sys.argv[6]=="yes": result["nested"]=worker.run_nested_probe(config)
print(json.dumps(result))
'''
        _, output = supervisor.tool([
            "/usr/bin/unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork",
            "--kill-child", "--propagation", "private", "/usr/bin/python3", "-I", "-S", "-B",
            "-c", code, str(Path(kernel.__file__).parent), str(directory),
            str(ROOT / "scripts/validation_ownership/sandbox_exec.py"),
            "yes" if alternatives else "no", json.dumps(RUNTIME), "yes" if nested else "no",
        ], timeout=15)
        return json.loads(output)

    def test_actual_private_runtime_view_preserves_all_required_aliases(self):
        broken = self.private_view(alternatives=False, nested=False)
        self.assertTrue(broken["missing"].startswith("/etc/alternatives/"))
        fixed = self.private_view(alternatives=True, nested=False)
        self.assertNotIn("missing", fixed)
        self.assertEqual(set(fixed["inside"]["programs"]), set(RUNTIME))
        for name, before in fixed["before"]["programs"].items():
            after = fixed["inside"]["programs"][name]
            self.assertEqual(before["resolved"], after["resolved"])
            self.assertEqual(before["identity"], after["identity"])
            self.assertEqual(
                [(item["path"], item["target"]) for item in before["links"]],
                [(item["path"], item["target"]) for item in after["links"]],
            )
            for old, new in zip(before["links"], after["links"]):
                if old["path"] not in {"/" + name for name in runtime_view.ROOT_ALIASES}:
                    self.assertEqual(old["identity"], new["identity"])

    def test_actual_nested_worker_uses_parent_validated_config_descriptor(self):
        observed = self.private_view(alternatives=True, nested=True)
        nested = observed["nested"]
        self.assertEqual(nested["uid_map"], ["0", "0", "1"])
        self.assertEqual(nested["no_new_privs"], "1")
        self.assertEqual(nested["config_handoff"]["parent_identity"][3], 0)
        self.assertIn("cgroup_control_probe", nested)
        self.assertNotEqual(
            nested["config_handoff"]["parent_user_namespace"],
            nested["config_handoff"]["user_namespace"],
        )
        self.assertEqual(observed["outer_uid_map"], ["0", str(os.getuid()), "1"])

    def test_real_unmapped_root_descriptor_is_bound_and_adversarial_receipts_reject(self):
        target = self.root / "readonly-config"
        target.touch()
        writable = self.root / "writable"
        writable.touch()
        parent = r'''
import json,os,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import kernel
source="/sys/devices/system/cpu/kernel_max"
fd,receipt=kernel.open_owned_config(source)
try:
    result=subprocess.run(
        ["/usr/bin/unshare","--user","--map-root-user","--mount","--propagation","private",
         "/usr/bin/python3","-I","-S","-B","-c",sys.argv[4],sys.argv[1],sys.argv[2],
         sys.argv[3],str(fd),json.dumps(receipt),source],
        pass_fds=(fd,),capture_output=True,timeout=8)
    if result.returncode: raise RuntimeError(result.stderr.decode()+result.stdout.decode())
    print(result.stdout.decode(),end="")
finally: os.close(fd)
'''
        child = r'''
import copy,json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import entry,kernel,policy
fd=int(sys.argv[4]);receipt=json.loads(sys.argv[5]);source=sys.argv[6]
ns=entry.load_namespace_helper(str(Path(sys.argv[1]).parent/"validation_ownership/sandbox_exec.py"))
ns.bind(source,sys.argv[2])
try: kernel.owned_config(sys.argv[2])
except policy.GuardError as error: original=str(error)
else: raise AssertionError("original ownership rejection was not reproduced")
value,proof=kernel.inherited_config(fd,receipt,sys.argv[2])
rejected=[]
foreign=os.open("/sys/devices/system/cpu/possible",os.O_RDONLY)
try:
    try: kernel.inherited_config(foreign,receipt,sys.argv[2])
    except policy.GuardError: rejected.append("foreign-unmapped")
    else: raise AssertionError("a different unmapped-root object was accepted")
finally: os.close(foreign)
for field in ("hash","inode"):
    bad=copy.deepcopy(receipt)
    if field=="hash": bad["sha256"]="0"*64
    else: bad["identity"][1]+=1
    try: kernel.inherited_config(fd,bad,sys.argv[2])
    except policy.GuardError: rejected.append(field)
    else: raise AssertionError("forged receipt accepted")
rw=os.open(sys.argv[3],os.O_RDWR)
try:
    try: kernel.inherited_config(rw,receipt,sys.argv[2])
    except policy.GuardError: rejected.append("writable")
    else: raise AssertionError("writable descriptor accepted")
finally: os.close(rw)
ns.bind("/sys/devices/system/cpu/possible",sys.argv[2])
try: kernel.inherited_config(fd,receipt,sys.argv[2])
except policy.GuardError: rejected.append("replaced-path")
else: raise AssertionError("replaced config path accepted")
print(json.dumps({"value":value,"proof":proof,"original":original,"rejected":rejected}))
'''
        _, output = supervisor.tool([
            "/usr/bin/python3", "-I", "-S", "-B", "-c", parent,
            str(Path(kernel.__file__).parent), str(target), str(writable), child,
        ], timeout=12)
        observed = json.loads(output)
        self.assertEqual(observed["value"], kernel.owned_config("/sys/devices/system/cpu/kernel_max"))
        self.assertEqual(observed["proof"]["parent_identity"][3], 0)
        self.assertNotEqual(observed["proof"]["visible_identity"][3], 0)
        self.assertEqual(set(observed["rejected"]), {"hash", "inode", "writable", "replaced-path", "foreign-unmapped"})


if __name__ == "__main__":
    unittest.main()
