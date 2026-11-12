# Allowlisted local observations; outbound HTTPS only.
require 'json'
require 'digest'
require 'net/http'
require 'open3'
require 'timeout'
require 'time'
require 'uri'

BACKUP_ID = /\A[a-f0-9]{64}\z/
SAFE_NAME = /\A[A-Za-z0-9][A-Za-z0-9_.@:+-]{0,127}\z/
TRANSPORT_ERROR = /\A[a-z][a-z0-9_]{0,63}\z/
TRANSPORT_MAX_AGE = 900
LAUNCHD_KEYS = %w[scheduler label plist plist_sha256 receipt
                  transport_receipt max_age_seconds].freeze

def run(*args)
  Timeout.timeout(8) { Open3.capture3(*args) }
rescue StandardError
  [nil,nil,nil]
end

def safe_path(value)
  return false unless value.is_a?(String) && value.start_with?('/') &&
    !value.match?(/[\x00-\x1f\x7f]/) && value.bytesize <= 1024
  parts = value.split('/', -1)
  parts.length > 1 && parts[0].empty? &&
    parts[1..].none? { |part| part.empty? || part == '.' || part == '..' }
end

def valid_launchd_job(job)
  return false unless job.is_a?(Hash) && job['scheduler'] == 'launchd' &&
    (job.keys - LAUNCHD_KEYS).empty? &&
    SAFE_NAME.match?(job['label'].to_s) &&
    safe_path(job['plist']) && safe_path(job['receipt']) &&
    job['plist_sha256'].is_a?(String) && job['plist_sha256'].match?(/\A[0-9a-f]{64}\z/) &&
    (!job.key?('transport_receipt') || safe_path(job['transport_receipt']))
  paths = [job['plist'], job['receipt'], job['transport_receipt']].compact
  paths.uniq.length == paths.length
end

def safe_backup_arguments(arguments, server)
  return false unless arguments.is_a?(Array) &&
    arguments.all? { |item| item.is_a?(String) }
  base = [
    '/usr/bin/ruby',
    '/Library/Restic/lib/restic-mac.rb',
    '--config',
    "/Library/Restic/config/#{server}.json",
    'backup'
  ]
  arguments == base ||
    arguments == [base[0], '-E', 'UTF-8', *base[1..]]
end

def controlled_public_file(path)
  before = File.lstat(path)
  raise 'UNCONTROLLED_FILE' unless before.file? && before.uid.zero? &&
    (before.mode & 0o7777) == 0o644 && before.size <= 65536
  flags = File::RDONLY
  flags |= File::NOFOLLOW if File.const_defined?(:NOFOLLOW)
  value = File.open(path, flags) do |io|
    opened = io.stat
    raise 'FILE_CHANGED' unless [opened.dev, opened.ino] == [before.dev, before.ino]
    io.read(65537)
  end
  after = File.lstat(path)
  raise 'FILE_CHANGED' unless value.bytesize <= 65536 &&
    [after.dev, after.ino, after.size, after.mtime, after.uid, after.mode] ==
    [before.dev, before.ino, before.size, before.mtime, before.uid, before.mode]
  value
end

def utc_stamp(value)
  return nil unless value.is_a?(String) &&
    value.match?(/\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)\z/)
  result = Time.iso8601(value)
  result.utc_offset.zero? ? result : nil
rescue ArgumentError
  nil
end

def launchd_schedule(job, server)
  label = job['label']
  plist = job['plist']
  pin = job['plist_sha256']
  return 'unknown' unless valid_launchd_job(job) &&
    server.is_a?(String) && SAFE_NAME.match?(server)
  output, error, status = run('/bin/launchctl', 'print', 'system/' + label)
  return 'unknown' unless status
  if !status.success?
    return 'down' if status.exitstatus == 113 && (output.to_s + error.to_s).include?('Could not find service')
    return 'unknown'
  end
  return 'unknown' unless output.to_s.match?(/\Asystem\/#{Regexp.escape(label)}\s*=\s*\{/) &&
    output.match?(/^\s*path = #{Regexp.escape(plist)}\s*$/)
  latest_exit = output[/^\s*last exit code = (\d+)\s*$/, 1]
  state = output[/^\s*state = ([^\n]+)$/, 1].to_s.strip
  return 'down' if latest_exit && latest_exit != '0' && state != 'running'
  disabled, _, disabled_status = run('/bin/launchctl', 'print-disabled', 'system')
  return 'unknown' unless disabled_status&.success? && disabled.to_s.include?('disabled services = {')
  overrides = disabled.to_s.lines.grep(/^\s*"#{Regexp.escape(label)}"\s*=>/)
  return 'unknown' if overrides.length > 1
  unless overrides.empty?
    override = overrides.first.match(/^\s*"#{Regexp.escape(label)}"\s*=>\s*(true|false|disabled|enabled)\s*$/)
    return 'unknown' unless override
    return 'down' if %w[true disabled].include?(override[1])
  end
  original = controlled_public_file(plist)
  return 'unknown' unless Digest::SHA256.hexdigest(original) == pin
  plan_json, _, plan_status = run('/usr/bin/plutil', '-convert', 'json', '-o', '-', plist)
  return 'unknown' unless plan_status&.success? && controlled_public_file(plist) == original
  plan = JSON.parse(plan_json)
  arguments = plan['ProgramArguments']
  working_directory = plan['WorkingDirectory']
  calendar = plan['StartCalendarInterval']
  return 'down' unless plan.is_a?(Hash) && plan['Label'] == label && plan['UserName'] == 'root' &&
    plan['GroupName'] == 'wheel' && plan['RunAtLoad'] == false && !plan['Disabled'] &&
    !plan['KeepAlive'] && safe_backup_arguments(arguments, server) &&
    working_directory == '/Library/Restic' &&
    calendar == {'Hour' => 4, 'Minute' => 0} &&
    !plan.key?('StartInterval') && !plan.key?('WatchPaths') &&
    !plan.key?('QueueDirectories') && !plan.key?('PathState') &&
    !plan.key?('StartOnMount')
  'up'
rescue StandardError
  'unknown'
end

def backup_state(job, server, now)
  return 'unknown' unless valid_launchd_job(job) &&
    server.is_a?(String) && SAFE_NAME.match?(server)
  schedule = launchd_schedule(job, server)
  return schedule unless schedule == 'up'
  receipt = JSON.parse(controlled_public_file(job.fetch('receipt')))
  return 'unknown' unless receipt.is_a?(Hash) && receipt['schema'] == 1 &&
    receipt['profile'] == server && %w[pending running failed success].include?(receipt['status'])
  return 'down' if receipt['status'] == 'failed'
  return 'unknown' if receipt['status'] == 'pending'
  success = utc_stamp(receipt['last_success'])
  checked = utc_stamp(receipt['last_check'])
  started = utc_stamp(receipt['attempt_started_utc'])
  return 'unknown' unless success && checked && started && receipt['error_code'].nil? &&
    receipt['snapshot_id'].is_a?(String) && receipt['snapshot_id'].match?(BACKUP_ID)
  check_mode = receipt['check_mode']
  return 'unknown' if check_mode.nil?
  return 'unknown' unless check_mode.is_a?(String) && !check_mode.empty?
  return 'down' unless check_mode == 'read-data'
  return 'unknown' if [success, checked, started].any? { |time| time > now + 300 }
  return 'unknown' unless checked >= success
  if receipt['status'] == 'success'
    finished = utc_stamp(receipt['attempt_finished_utc'])
    return 'unknown' unless finished && finished >= started &&
      (finished - success).abs < 1 && finished <= now + 300
  else
    return 'unknown' unless receipt['attempt_finished_utc'].nil? && started >= success - 300
  end
  max_age = job.fetch('max_age_seconds', 93_600)
  return 'unknown' unless max_age.is_a?(Numeric) && max_age >= 60 && max_age <= 604_800
  return 'down' if [success, checked].any? { |time| now - time > max_age }
  receipt['status'] == 'running' ? 'maintenance' : 'up'
rescue StandardError
  'unknown'
end

def transport_metadata(path, server, now)
  return nil unless path.is_a?(String) && !path.empty?
  receipt = JSON.parse(controlled_public_file(path))
  return nil unless receipt.is_a?(Hash) && receipt['schema'] == 1 &&
    receipt['profile'] == server && %w[up down unknown].include?(receipt['status'])
  checked = utc_stamp(receipt['checked_utc'])
  return nil unless checked && checked <= now + 300
  unauth = receipt['unauthenticated_status']
  config = receipt['authenticated_config_status']
  error = receipt['error_code']
  return nil unless [nil, 401].include?(unauth) && [nil, 'pinned'].include?(config)
  return nil if !error.nil? && (!error.is_a?(String) || !error.match?(TRANSPORT_ERROR))
  return nil if receipt['status'] == 'up' && (unauth != 401 || config != 'pinned' || !error.nil?)
  state = receipt['status']
  if now - checked > TRANSPORT_MAX_AGE
    state = 'down'
    error = 'stale' if error.nil?
  end
  {'checked_at' => checked.to_f, 'state' => state,
   'unauthenticated_status' => unauth, 'config_status' => config, 'error_code' => error}
rescue StandardError
  {'checked_at' => now.to_f, 'state' => 'unknown',
   'unauthenticated_status' => nil, 'config_status' => nil,
   'error_code' => 'receipt_invalid'}
end

def backup_metadata(job, server, now)
  return nil unless valid_launchd_job(job) &&
    server.is_a?(String) && SAFE_NAME.match?(server)
  receipt = JSON.parse(controlled_public_file(job.fetch('receipt')))
  return nil unless receipt.is_a?(Hash) && receipt['schema'] == 1 &&
    receipt['profile'] == server && %w[success running].include?(receipt['status']) &&
    receipt['check_mode'] == 'read-data' &&
    receipt['error_code'].nil? &&
    receipt['snapshot_id'].is_a?(String) && receipt['snapshot_id'].match?(BACKUP_ID)
  success = utc_stamp(receipt['last_success'])
  checked = utc_stamp(receipt['last_check'])
  started = utc_stamp(receipt['attempt_started_utc'])
  return nil unless success && checked && started && checked >= success &&
    checked <= now + 300
  if receipt['status'] == 'success'
    finished = utc_stamp(receipt['attempt_finished_utc'])
    return nil unless finished && finished >= started &&
      (finished - success).abs < 1 && finished <= now + 300
  else
    return nil unless receipt['attempt_finished_utc'].nil? && started >= success - 300
  end
  result = {'schema_version' => 1, 'checked_at' => checked.to_f,
            'snapshot_id' => receipt['snapshot_id'],
            'check_mode' => receipt['check_mode']}
  transport = transport_metadata(job['transport_receipt'], server, now)
  result['transport'] = transport if transport
  result
rescue StandardError
  nil
end

def collect(config)
  output,_,exit_status = run(config.fetch('docker','/usr/local/bin/docker'),
                             'ps','-a','--format','{{.Names}}|{{.State}}|{{.Status}}')
  states = {}
  if exit_status&.success?
    output.lines.each do |line|
      name,state,status = line.strip.split('|',3)
      states[name] = status.include?('unhealthy') ? 'degraded' : state == 'running' ? 'up' : 'down'
    end
  end
  services = {}
  config.fetch('services').each do |service|
    state = 'unknown'
    metadata = nil
    if service['id'].end_with?(':collector')
      state = 'up'
    elsif service.key?('backup_job')
      state = backup_state(service['backup_job'], config['server'], Time.now)
      metadata = backup_metadata(service['backup_job'], config['server'], Time.now)
    elsif service['containers'] && !service['containers'].empty?
      values = service['containers'].map do |name|
        states.fetch(name, exit_status&.success? ? 'down' : 'unknown')
      end
      state = %w[down degraded unknown up].find { |value| values.include?(value) } || 'unknown'
    elsif service['process']
      _,_,status = run('/usr/bin/pgrep','-x',service['process'])
      state = status.nil? ? 'unknown' : status.success? ? 'up' : status.exitstatus == 1 ? 'down' : 'unknown'
    end
    state = 'unknown' if %w[up maintenance].include?(state) && metadata.nil? &&
      service.key?('backup_job')
    item = {'state' => state}
    item['backup'] = metadata if metadata
    services[service.fetch('id')] = item
  end
  metrics = {}
  output,_,status = run('/bin/df','-Pk','/')
  metrics['disk_percent'] = output.lines.last.split[4].to_f if status&.success?
  output,_,status = run('/usr/sbin/sysctl','-n','vm.loadavg')
  metrics['load1'] = output.delete('{}').split.first.to_f if status&.success?
  output,_,status = run('/usr/sbin/sysctl','-n','kern.boottime')
  metrics['uptime_seconds'] = Time.now.to_f-output[/sec = (\d+)/,1].to_i if status&.success? && output[/sec = (\d+)/,1]
  {'services' => services, 'metrics' => metrics}
end

def send_status(config)
  uri = URI(config.fetch('endpoint'))
  raise 'HTTPS required' unless uri.scheme == 'https'
  http = Net::HTTP.new(uri.host,uri.port)
  http.use_ssl = true
  http.open_timeout = 10
  http.read_timeout = 10
  request = Net::HTTP::Post.new(uri)
  request['Content-Type'] = 'application/json'
  request['Authorization'] = 'Bearer ' + config.fetch('token')
  request['X-Server-Profile'] = config.fetch('server')
  request.body = JSON.generate(collect(config))
  response = http.request(request)
  raise 'Ingest rejected' unless response.code == '200'
  response
end

def main
  config = JSON.parse(File.read(ARGV.fetch(0)))
  if ARGV.include?('--once')
    response = send_status(config)
    puts "INGEST #{response.code} #{response.body}"
  else
    loop do
      begin
        send_status(config)
      rescue StandardError
        warn 'Status delivery failed; retry next interval'
      end
      sleep 60
    end
  end
end

main if $PROGRAM_NAME == __FILE__
