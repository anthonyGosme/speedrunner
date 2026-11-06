cd /tmp; python3 --version; python3 - <<P
import urllib.request, zipfile, os, stat
urllib.request.urlretrieve("https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip", "/tmp/a.zip")
z = zipfile.ZipFile("/tmp/a.zip")
for i in z.infolist():
    z.extract(i, "/tmp"); m = i.external_attr >> 16
    if m: os.chmod(os.path.join("/tmp", i.filename), m & 0o777)
P
rm -f a.zip; ./aws/install -i /opt/aws -b /opt/bin >/dev/null; D=$(ls -d /opt/aws/v2/2*)/dist; $D/aws --version; python3 -I /c/ceiling.py $D 11
