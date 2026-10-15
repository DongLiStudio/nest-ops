# Allowlisted local observations; outbound HTTPS only.
require 'json'
require 'net/http'
require 'open3'
require 'timeout'
require 'uri'
CONFIG = JSON.parse(File.read(ARGV.fetch(0)))
def run(*args)
  Timeout.timeout(8) { Open3.capture3(*args) }
rescue StandardError
  [nil,nil,nil]
end
def collect
  output,_,exit_status = run(CONFIG.fetch('docker','/usr/local/bin/docker'),'ps','-a','--format','{{.Names}}|{{.State}}|{{.Status}}')
  states = {}
  if exit_status&.success?
    output.lines.each do |line|
      name,state,status=line.strip.split('|',3)
      states[name]=status.include?('unhealthy') ? 'degraded' : state=='running' ? 'up' : 'down'
    end
  end
  services={}
  CONFIG.fetch('services').each do |service|
    state='unknown'
    if service['id'].end_with?(':collector')
      state='up'
    elsif service['containers'] && !service['containers'].empty?
      values=service['containers'].map { |name| states.fetch(name,exit_status&.success? ? 'down' : 'unknown') }
      state=%w[down degraded unknown up].find { |value| values.include?(value) } || 'unknown'
    elsif service['process']
      _,_,status=run('/usr/bin/pgrep','-x',service['process'])
      state=status.nil? ? 'unknown' : status.success? ? 'up' : status.exitstatus==1 ? 'down' : 'unknown'
    end
    services[service.fetch('id')]={'state'=>state}
  end
  metrics={}
  output,_,status=run('/bin/df','-Pk','/')
  metrics['disk_percent']=output.lines.last.split[4].to_f if status&.success?
  output,_,status=run('/usr/sbin/sysctl','-n','vm.loadavg')
  metrics['load1']=output.delete('{}').split.first.to_f if status&.success?
  output,_,status=run('/usr/sbin/sysctl','-n','kern.boottime')
  metrics['uptime_seconds']=Time.now.to_f-output[/sec = (\d+)/,1].to_i if status&.success? && output[/sec = (\d+)/,1]
  {'services'=>services,'metrics'=>metrics}
end
def send_status
  uri=URI(CONFIG.fetch('endpoint'))
  raise 'HTTPS required' unless uri.scheme=='https'
  http=Net::HTTP.new(uri.host,uri.port);http.use_ssl=true
  http.open_timeout=10;http.read_timeout=10
  request=Net::HTTP::Post.new(uri)
  request['Content-Type']='application/json'
  request['Authorization']='Bearer '+CONFIG.fetch('token')
  request['X-Server-Profile']=CONFIG.fetch('server')
  request.body=JSON.generate(collect)
  response=http.request(request)
  raise 'Ingest rejected' unless response.code=='200'
  response
end
if ARGV.include?('--once')
  response=send_status
  puts "INGEST #{response.code} #{response.body}"
else
  loop do
    begin
      send_status
    rescue StandardError
      warn 'Status delivery failed; retry next interval'
    end
    sleep 60
  end
end
