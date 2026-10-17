"""Public packages live in Releases (Git rejects files >100 MB), metadata in Git.
This file is also copied into the existing public repository. No private keys.
"""
import argparse,hashlib,json,re,shutil,subprocess,tempfile
from pathlib import Path
REPOSITORY='leiteigor2020-svg/pitsystem-updates'
TAG='update-packages-beta'
STABLE_TAG='update-packages-stable'
TREES=('tuf','production/tuf')

def run(*args):
    return subprocess.run(list(args),check=True,capture_output=True,text=True)

def packages(checkout):
    for tree in TREES:
        metadata=Path(checkout)/tree/'metadata/targets.json'
        if not metadata.exists():continue
        for name,entry in json.loads(metadata.read_text())['signed']['targets'].items():
            if not name.startswith('packages/'):continue
            if not re.fullmatch(r'packages/ParafusoGestor-[0-9]+\.[0-9]+\.[0-9]+-windows-x64\.zip',name):
                raise ValueError('Invalid public package path')
            yield Path(checkout)/tree/'targets'/name,entry

def verify(path,entry):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    size=path.stat().st_size;sha=digest.hexdigest()
    if size!=entry['length'] or sha!=entry['hashes']['sha256']:
        raise ValueError(f"Public package hash/size mismatch: {path.name}; expected size={entry['length']} sha256={entry['hashes']['sha256']}; actual size={size} sha256={sha}")

def asset_tag(path,checkout):
    return STABLE_TAG if path.relative_to(Path(checkout)).parts[0]=='production' else TAG

def storage_name(path,entry):
    # Release storage identity includes bytes; the TUF target/URL stays unchanged.
    sha=entry['hashes']['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('Invalid public package SHA-256')
    return sha+'-'+path.name

def download_verified(tag,name,path,entry):
    with tempfile.TemporaryDirectory() as tmp:
        run('gh','release','download',tag,'--repo',REPOSITORY,'--pattern',name,'--dir',tmp)
        source=Path(tmp)/name
        verify(source,entry)
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,path)

def hydrate(checkout):
    names_by_tag={}
    for path,entry in packages(checkout):
        if not path.exists():
            tag=asset_tag(path,checkout)
            if tag not in names_by_tag:
                assets=json.loads(run('gh','release','view',tag,'--repo',REPOSITORY,'--json','assets').stdout)['assets']
                names_by_tag[tag]={a['name'] for a in assets}
            name=storage_name(path,entry)
            # Backward compatibility with packages already published under version-only names.
            if name not in names_by_tag[tag]:name=path.name
            download_verified(tag,name,path,entry)
        verify(path,entry)

def upload_verified(tag,name,path,entry):
    with tempfile.TemporaryDirectory() as tmp:
        upload=Path(tmp)/name
        shutil.copyfile(path,upload)
        verify(upload,entry)
        run('gh','release','upload',tag,str(upload),'--repo',REPOSITORY)
        # Check actual remote bytes before permitting metadata publication.
        download_verified(tag,name,Path(tmp)/'verified.zip',entry)

def stage(checkout):
    checkout=Path(checkout)
    # Fail closed on token/network failures, and create storage only on confirmed absence.
    releases=run('gh','release','list','--repo',REPOSITORY,'--limit','100','--json','tagName').stdout
    records=list(packages(checkout))
    for tag in sorted({asset_tag(path,checkout) for path,_ in records}):
        if tag not in [x['tagName'] for x in json.loads(releases)]:
            run('gh','release','create',tag,'--repo',REPOSITORY,'--title','PitSystem '+tag,'--notes','Immutable packages authenticated by TUF.','--latest=false')
        assets=json.loads(run('gh','release','view',tag,'--repo',REPOSITORY,'--json','assets').stdout)['assets']
        names={a['name'] for a in assets}
        for path,entry in records:
            if asset_tag(path,checkout)!=tag:continue
            verify(path,entry)
            name=storage_name(path,entry)
            if name in names:
                with tempfile.TemporaryDirectory() as tmp:
                    download_verified(tag,name,Path(tmp)/name,entry)
            elif path.name in names:
                # A failed earlier publish may leave an orphan from another build
                # of the same version. Never replace it or change TUF to match it.
                with tempfile.TemporaryDirectory() as tmp:
                    run('gh','release','download',tag,'--repo',REPOSITORY,'--pattern',path.name,'--dir',tmp)
                    try:verify(Path(tmp)/path.name,entry)
                    except ValueError:
                        print('Existing version-only asset differs; storing verified build as '+name)
                    else:continue
                upload_verified(tag,name,path,entry)
                names.add(name)
            else:
                upload_verified(tag,name,path,entry)
                names.add(name)
    (checkout/'.github/workflows').mkdir(parents=True,exist_ok=True)
    (checkout/'tools').mkdir(exist_ok=True)
    shutil.copyfile(Path(__file__).parent/'public-repository/deploy-updates.yml',checkout/'.github/workflows/deploy-updates.yml')
    shutil.copyfile(__file__,checkout/'tools/public_update_store.py')
    ignores=checkout/'.gitignore'
    current=ignores.read_text() if ignores.exists() else ''
    for rule in ('/tuf/targets/packages/','/production/tuf/targets/packages/'):
        if rule not in current.splitlines():current=current.rstrip()+'\n'+rule+'\n'
    ignores.write_text(current)
    (checkout/'.nojekyll').touch()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['hydrate','stage']);parser.add_argument('--checkout',required=True);a=parser.parse_args()
    try:globals()[a.command](a.checkout)
    except subprocess.CalledProcessError:raise SystemExit('Public update storage failed. Check repository token, permissions and release assets.')
