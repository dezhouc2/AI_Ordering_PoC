import audioop
import base64
import json
import os
from flask import Flask, request
from flask_sock import Sock, ConnectionClosed
from twilio.twiml.voice_response import VoiceResponse, Start
from twilio.rest import Client
import vosk
import boto3
import time
from pydub import AudioSegment
from pydub.generators import Sine
from gtts import gTTS
import os
import io
import count
from datetime import datetime
import requests

app = Flask(__name__)
sock = Sock(app)
twilio_client = Client()
model = ''
brt = boto3.client(service_name='bedrock-runtime')
modelId = 'anthropic.claude-instant-v1'
accept = 'application/json'
contentType = 'application/json'
audio_path = '/home/ec2-user/websocketapi/vosk-live-transcription/temp.mp3'
chathistory = ""


CL = '\x1b[0K'
BS = '\x08'


json_schema = [
    {
        "name": "order item from customer",
        "quantity": 0,
        "price": "corresponding price of each item",
    }
]
 
json_schema_str = json.dumps(json_schema)

menu_data = """
Items	Price
Coke	2.00
Chicken Sandwich  4.51
HamBurger 5.29
"""

@app.route('/call', methods=['POST'])
def call():
    """Accept a phone call."""
    response = VoiceResponse()
    start = Start()
    start.stream(url=f'wss://{request.host}/vosk')
    response.append(start)
    response.say('Please leave a message')
    response.pause(length=60)
    print(f'Incoming call from {request.form["From"]}')
    return str(response), 200, {'Content-Type': 'text/xml'}


def llm(userInput,connectId):
    #talkHistoryJson = dynamodb_search(connectId)
    global chathistory
    promote = ''
    tips = (f'You are a helpful assistant from Sunrise restaurant that is capable of managing and taking complex food orders.'
          + f'When the order is complete, final order output response should be formatted in this JSON schema {json_schema_str},only extract value of the item name mapped to items in the menu into the json output, the first character must be capital'
          + "Do not show me the menu until I ask for it"
          + f'When the customer ask about the menu. Customers are limited to ordering items listed on the menu {menu_data}, do not output the two words: Items and Price. Save item, quantity and corresponding price into the json output.'
          + 'Keep your responses brief, and refrain from suggesting other items or asking unrelated questions.'
          + 'After customer ordered, you will conclude with fixed sentence is in the format "Okay. Your order of item is price,We are preparing it for you. Please enjoy your food. Goodbye!" the item and price is from customer order' 
          + "The output only consists of two lines separated by '*' , the first one is the concluded sentece. The second part is the json output"
    )
    body = json.dumps({
        "prompt": "\n\nHuman:"+ tips +userInput+". \n\nAssistant:",
        "max_tokens_to_sample": 300,
        "temperature": 0.1,
        "top_p": 0.9,
    })
    '''
    if talkHistoryJson=='':
        dynamodb_inserth(connectId,'Human',''
                   +tips
                   +' Now, my question is '+userInput)
        promote = ('Human:'+tips+'Now, my question is '+userInput+'. Assistant:')
    else:

        #dynamodb_inserth(connectId,'Human',userInput)
        promote = ('Human:I\'m a customer. You are sunrise restaurant\'s waiter.'
                   +'My question is  '+userInput+'. Please answer me. The json behind is our conversation history. Please refer to them to answer.'+talkHistoryJson+'. Assistant:')
    print("bedrock start======")
    '''
    """
    promote = ('Human:I\'m a customer. You are sunrise restaurant\'s waiter.'
                   + "the background is " + tips + "chat history is:" +chathistory
                   +'My question is  '+userInput+'. Please answer me in short. Assistant:')
    """
    chathistory = chathistory +"Customer:" + userInput +"\n"
    """
    body = json.dumps({
        "prompt": promote,
        "max_tokens_to_sample": 300,
        "temperature": 0.1,
        "top_p": 0.9,
    })
    """
    #print("question is "+promote)
    response = brt.invoke_model(body=body, modelId=modelId, accept=accept, contentType=contentType)
    response_body = json.loads(response.get('body').read())
    chathistory = chathistory + "Agent:" + response_body.get('completion') +"\n"
    #print(response_body.get("completion").strip())
    model_output = response_body.get("completion").strip()
    # text
    #dynamodb_inserth(connectId, 'Assistant', response_body.get('completion'))
    return model_output

# use api to switch the ASR model
@sock.route('/vosk')
def stream(ws):

    """Receive and transcribe audio stream."""
    model = vosk.Model('modeldanzu')
    rec = vosk.KaldiRecognizer(model, 64000)
    time_last_word = None
    voice_flag = True
    user_speak = True
    response =''
    url = "http://54.174.193.122:5000/UploadOrder"
    
    
    while voice_flag:
        message = ws.receive()
        packet = json.loads(message)
        if packet['event'] == 'start':
            print('Streaming is starting')
            stream_buffer = ''
            stream_base64 = process_text_to_ulaw("Welcome! What can I get for you?")
            global chathistory
            chathistory = chathistory + "Agent: Welcome! What can I get for you?" + "\n"
            json_obj = {
                "event": "media",
                "streamSid": packet['streamSid'],
                "media": {
                    "payload": stream_base64
                }
            }
            ws.send(json.dumps(json_obj)) 
        elif packet['event'] == 'stop':
            print('\nStreaming has stopped')
        elif packet['event'] == 'media' and user_speak:
            audio = base64.b64decode(packet['media']['payload'])
            audio = audioop.ulaw2lin(audio, 2)
            audio = audioop.ratecv(audio, 2, 1, 8000, 64000, None)[0]
            if rec.AcceptWaveform(audio):
                r = json.loads(rec.Result())
                time_last_word = time.time()
                
                stream_buffer = r['text']
                if stream_buffer:
                    print("Customer:"+ r['text'].strip())

            else:
                r = json.loads(rec.PartialResult())
                

            if time_last_word and (time.time() - time_last_word > 1):
                voice_flag = False
                if stream_buffer:
                    response = llm(stream_buffer,packet['streamSid']) #llm call api.
                    #print("LLM Response:", response)
                    #print(response)
                    lines = response.split('*')
                    print("LLM Response:",lines[0].strip())
                    if len(lines) > 1:
                        uptrillion_order = lines[1]
                        response = requests.post(url, json=json.loads(lines[1]))
                        
                        print(response.status_code)
                        if response.status_code == 200:
                            print("Succeed！")
                        print("Call Uptrillion api:",lines[1])
                    response = lines[0]
                    #stream_base64 = text_to_audio_base64(response)
                    stream_base64 = process_text_to_ulaw(response)
                    audio_length_ms = get_audio_length(audio_path)
                    json_obj = {
                        "event": "media",
                        "streamSid": packet['streamSid'],
                        "media": {
                            "payload": stream_base64
                        }
                    }
                    #print(datetime.fromtimestamp(time.time()))
                    

                    user_speak = False
                    ws.send(json.dumps(json_obj)) 
                    
                    if "Goodby" in response:
                        time.sleep(audio_length_ms/1000+1)
                        ws.close()
                    #print(audio_length_ms)
                    
                    user_speak = True
                    audio = ''
                    stream_buffer = ''
                    #rec = vosk.KaldiRecognizer(model, 16000)
                    #print(datetime.fromtimestamp(time.time()))
                    # ws push/start
                #reset_recognizer()
                stream_buffer = ''
                user_speak = True
                time_last_word = None
                voice_flag = True
                
                #need to run the call receiving and call reponse not at the same time.
                #need to add a call interupt mechanism.



def text_to_audio_base64(text):
    '''
    audio = Sine(1000).to_audio_segment(duration=len(text) * 100)  # 生成一个持续时间为文本长度的音频，这里使用了一个简单的示例音频，您可以自定义音频生成方式

    audio = audio._spawn(audio.raw_data, overrides={'frame_rate': 8000, 'channels': 1, 'sample_width': 1})
    audio = audio.set_frame_rate(8000).set_channels(1)

    audio_base64 = base64.b64encode(audio.raw_data)

    return audio_base64
    '''
def get_audio_length(audio_path):
    audio_length_ms = count.get_audio_length(audio_path)
    return audio_length_ms

def process_text_to_ulaw(text):
    # Convert text to speech
    tts = gTTS(text)
    tts.save("temp.mp3")

    # Convert mp3 to wav
    audio = AudioSegment.from_file('/home/ec2-user/websocketapi/vosk-live-transcription/temp.mp3', format='mp3')
    
    # Change sample rate to 8000 Hz and convert to ulaw
    audio = audio.set_frame_rate(8000)
    buffer = io.BytesIO()
    audio.export(buffer, format="mulaw")

    # Encode in base64
    audio_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    return audio_base64

def dynamodb_inserth(lexChat,type,content):
    timestamp = int(time.mktime(datetime.now().timetuple()))
    dynamodb = boto3.client('dynamodb')
    table_name = 'bedrockHistory'
    item = {
        'lexChat': {'S': lexChat},
        'chatNum': {'N': str(timestamp)},
        'content': {'S': content},
        'type': {'S': type}
    }
    response = dynamodb.put_item(
        TableName=table_name,
        Item=item
    )


def dynamodb_search(partition_key_value):
    dynamodb = boto3.client('dynamodb')
    table_name = 'bedrockHistory'
    try:
        key_condition_expression = 'lexChat = :pkval'
        expression_attribute_values = {
            ':pkval': {'S': partition_key_value}
        }

        response = dynamodb.query(
            TableName=table_name,
            KeyConditionExpression=key_condition_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ScanIndexForward=True
        )

        items = response['Items']

        str='{'
        for item in items:
            content = item.get('content').get('S')
            type = item.get('type').get('S')
            str = str + '"'+type+'":"'+content+'",'
        str = str + '}'
        if len(items)==0:
           str = ''
        print('dynamodb查询结果===============')
        print(str)
        return str

    except Exception as e:
        print(f"查询 DynamoDB 表发生错误：{str(e)}")


if __name__ == '__main__':
    '''
    os.environ["FFPROBE_PATH"] = "/home/ec2-user/vosk-live-transcription"
    text_1 = process_text_to_ulaw("hello")
    '''
    from pyngrok import ngrok
    port = 8443
    public_url = ngrok.connect(port, bind_tls=True,subdomain = "paxai").public_url
    print(public_url)
    number = twilio_client.incoming_phone_numbers.list()[0]
    number.update(voice_url=public_url + '/call')
    print(f'Waiting for calls on {number.phone_number}')
    
    app.run(port=port)